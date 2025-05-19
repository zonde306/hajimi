import asyncio
from fastapi import HTTPException, Request
from app.models.schemas import ChatCompletionRequest
from app.services import GeminiClient
from app.utils import update_api_call_stats
from app.utils.error_handling import handle_gemini_error
from app.utils.logging import log
import app.config.settings as settings
from typing import Literal
from app.utils.response import gemini_from_text, openAI_from_Gemini, openAI_from_text, combine_from_openai
from app.utils.stats import get_api_key_usage
from fastapi.responses import StreamingResponse
import json

# 非流式请求处理函数
async def process_nonstream_request(
    chat_request: ChatCompletionRequest,
    contents,
    system_instruction,
    current_api_key: str,
    response_cache_manager,
    safety_settings,
    safety_settings_g2,
    cache_key: str
):
    """处理非流式API请求"""
    gemini_client = GeminiClient(current_api_key)
    # 创建调用 Gemini API 的主任务
    gemini_task = asyncio.create_task(
        gemini_client.complete_chat(
            chat_request,
            contents,
            safety_settings_g2 if 'gemini-2.5' in chat_request.model else safety_settings,
            system_instruction
        )
    )
    # 使用 shield 保护任务不被外部轻易取消
    shielded_gemini_task = asyncio.shield(gemini_task)

    try:
        # 等待受保护的 API 调用任务完成
        response_content = await shielded_gemini_task
        response_content.set_model(chat_request.model)
        
        # 检查响应内容是否为空
        if not response_content or not response_content.text:
            log('warning', f"API密钥 {current_api_key[:8]}... 返回空响应",
                extra={'key': current_api_key[:8], 'request_type': 'non-stream', 'model': chat_request.model})
            return "empty"
        
        # 缓存响应结果
        await response_cache_manager.store(cache_key, response_content)
        # 更新 API 调用统计
        await update_api_call_stats(settings.api_call_stats, endpoint=current_api_key, model=chat_request.model,token=response_content.total_token_count)
        
        return "success"

    except Exception as e:
        # 处理 API 调用过程中可能发生的任何异常
        handle_gemini_error(e, current_api_key) 
        return "error" 
    
    
# 处理 route 中发起请求的函数
async def process_request(
    chat_request,
    key_manager,
    response_cache_manager,
    safety_settings,
    safety_settings_g2,
    cache_key: str
):
    """处理非流式请求"""
    format_type = getattr(chat_request, 'format_type', None)
    is_gemini = format_type and (format_type == "gemini")
    max_retry_num = max(round(settings.MAX_RETRY_NUM / chat_request.n), 1)
    max_concurrent_requests = max(round(settings.MAX_CONCURRENT_REQUESTS / chat_request.n), 1)

    async def generate():
        global current_api_key

        # 设置初始并发数
        current_concurrent = settings.CONCURRENT_REQUESTS
        
        # 当前请求次数
        current_try_num = 0
        
        # 空响应计数
        empty_response_count = 0

        # 尝试使用不同API密钥，直到达到最大重试次数或空响应限制
        while (current_try_num < max_retry_num) and (empty_response_count < settings.MAX_EMPTY_RESPONSES):
            # 获取当前批次的密钥数量
            batch_num = min(max_retry_num - current_try_num, current_concurrent)
            
            # 获取当前批次的密钥
            valid_keys = []
            checked_keys = set()  # 用于记录已检查过的密钥
            all_keys_checked = False  # 标记是否已检查所有密钥
            
            # 尝试获取足够数量的有效密钥
            while len(valid_keys) < batch_num:
                api_key = await key_manager.get_available_key()
                if not api_key:
                    break
                    
                # 如果这个密钥已经检查过，说明已经检查了所有密钥
                if api_key in checked_keys:
                    all_keys_checked = True
                    break
                
                checked_keys.add(api_key)
                # 获取API密钥的调用次数
                usage = await get_api_key_usage(settings.api_call_stats, api_key)
                # 如果调用次数小于限制，则添加到有效密钥列表
                if usage < settings.API_KEY_DAILY_LIMIT:
                    valid_keys.append(api_key)
                else:
                    log('warning', f"API密钥 {api_key[:8]}... 已达到每日调用限制 ({usage}/{settings.API_KEY_DAILY_LIMIT})",
                        extra={'key': api_key[:8], 'request_type': 'non-stream', 'model': chat_request.model})
            
            # 如果已经检查了所有密钥且没有找到有效密钥，则重置密钥栈
            if all_keys_checked and not valid_keys:
                log('warning', "所有API密钥已达到每日调用限制，重置密钥栈",
                    extra={'request_type': 'non-stream', 'model': chat_request.model})
                key_manager._reset_key_stack()
                # 重置后重新获取一个密钥
                api_key = await key_manager.get_available_key()
                if api_key:
                    valid_keys = [api_key]
            
            # 如果没有获取到任何有效密钥，跳出循环
            if not valid_keys:
                break
                
            # 更新当前尝试次数
            current_try_num += len(valid_keys)
            
            # 创建并发任务
            tasks = []
            tasks_map = {}
            for api_key in valid_keys:
                # 记录当前尝试的密钥信息
                log('info', f"非流式请求开始，使用密钥: {api_key[:8]}...", 
                    extra={'key': api_key[:8], 'request_type': 'non-stream', 'model': chat_request.model})
                
                if is_gemini:
                    contents, system_instruction = None, None
                else:
                    contents, system_instruction = GeminiClient.convert_messages(GeminiClient, chat_request.messages,model=chat_request.model)

                # 创建任务
                task = asyncio.create_task(
                    process_nonstream_request(
                        chat_request,
                        contents,
                        system_instruction,
                        api_key,
                        response_cache_manager,
                        safety_settings,
                        safety_settings_g2,
                        cache_key
                    )
                )
                tasks.append((api_key, task))
                tasks_map[task] = api_key
            
            # 等待所有任务完成或找到成功响应
            success = False
            while tasks and not success:
                # 短时间等待任务完成
                done, pending = await asyncio.wait(
                    [task for _, task in tasks],
                    return_when=asyncio.FIRST_COMPLETED
                )
                # 检查已完成的任务是否成功
                for task in done:
                    api_key = tasks_map[task]
                    try:
                        status = task.result()                    
                        # 如果有成功响应内容
                        if status == "success" :  
                            success = True
                            log('info', f"非流式请求成功", 
                                extra={'key': api_key[:8],'request_type': 'non-stream', 'model': chat_request.model})
                            cached_response, cache_hit = await  response_cache_manager.get_and_remove(cache_key)
                            if is_gemini :
                                return cached_response.data
                            else:
                                return openAI_from_Gemini(cached_response,stream=False)
                        elif status == "empty":
                            # 增加空响应计数
                            empty_response_count += 1
                            log('warning', f"空响应计数: {empty_response_count}/{settings.MAX_EMPTY_RESPONSES}",
                                extra={'key': api_key[:8], 'request_type': 'non-stream', 'model': chat_request.model})
                    
                    except Exception as e:
                        handle_gemini_error(e, api_key)
                    
                    # 更新任务列表，移除已完成的任务
                    tasks = [(k, t) for k, t in tasks if not t.done()]
                    
            # 如果当前批次没有成功响应，并且还有密钥可用，则继续尝试
            if not success and valid_keys:
                # 增加并发数，但不超过最大并发数
                current_concurrent = min(current_concurrent + settings.INCREASE_CONCURRENT_ON_FAILURE, max_concurrent_requests)
                log('info', f"所有并发请求失败或返回空响应，增加并发数至: {current_concurrent}", 
                    extra={'request_type': 'non-stream', 'model': chat_request.model})
            
            # 如果空响应次数达到限制，跳出循环，并返回酒馆正常响应(包含错误信息)
            if empty_response_count >= settings.MAX_EMPTY_RESPONSES:
                log('warning', f"空响应次数达到限制 ({empty_response_count}/{settings.MAX_EMPTY_RESPONSES})，停止轮询",
                    extra={'request_type': 'non-stream', 'model': chat_request.model})
                
                if is_gemini :
                    return gemini_from_text(content="空响应次数达到上限\n\n请尝试更换预设、角色卡或修改聊天消息",finish_reason="ERROR",stream=False)
                else:
                    return openAI_from_text(model=chat_request.model,content="空响应次数达到上限\n\n请尝试更换预设、角色卡或修改聊天消息",finish_reason="ERROR",stream=False)
        
        # 如果所有尝试都失败
        log('error', "API key 替换失败，所有API key都已尝试，请重新配置或稍后重试", extra={'request_type': 'switch_key'})
        
        if is_gemini:
            return gemini_from_text(content="所有API密钥均请求失败\n具体错误请查看轮询日志\n\n请尝试更换预设、角色卡或修改聊天消息",finish_reason="ERROR",stream=False)
        else:
            return openAI_from_text(model=chat_request.model,content="所有API密钥均请求失败\n具体错误请查看轮询日志\n\n请尝试更换预设、角色卡或修改聊天消息",finish_reason="ERROR",stream=False)

    async def process():
        workers = { asyncio.Task(generate()) for _ in range(min(chat_request.n, max_concurrent_requests)) }
        while not all([worker.done() for worker in workers]):
            await asyncio.wait(workers, timeout=settings.FAKE_STREAMING_INTERVAL, return_when=asyncio.ALL_COMPLETED)
            yield "\n"
        
        responses = [worker.result() for worker in workers]
        avaiable = [ x for x in responses if isinstance(x, dict) and x["choices"] and x["choices"][0]["finish_reason"] != "ERROR" ]
        if avaiable:
            combined = combine_from_openai(avaiable)
            yield json.dumps(combined, separators=(',', ':'))
        elif responses:
            yield json.dumps(responses[0], separators=(',', ':'))

    
    return StreamingResponse(process())
