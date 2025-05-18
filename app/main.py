from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import ErrorResponse
from app.services import GeminiClient
from app.utils import (
    APIKeyManager, 
    test_api_key, 
    ResponseCacheManager,
    ActiveRequestsManager,
    check_version,
    schedule_cache_cleanup,
    handle_exception,
    log
)
from app.config.persistence import save_settings, load_settings
from app.api import router, init_router, dashboard_router, init_dashboard_router
from app.vertex.vertex_ai_init import init_vertex_ai
from app.vertex.credentials_manager import CredentialManager
import app.config.settings as settings
from app.config.safety import SAFETY_SETTINGS, SAFETY_SETTINGS_G2
import asyncio
import sys
import pathlib
import os
# 设置模板目录
BASE_DIR = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(limit="50M")

# --------------- CORS 中间件 ---------------
# 如果 ALLOWED_ORIGINS 为空列表，则不允许任何跨域请求
if settings.ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --------------- 全局实例 ---------------
load_settings()
# 初始化API密钥管理器
key_manager = APIKeyManager()

# 创建全局缓存字典，将作为缓存管理器的内部存储
response_cache = {}

# 初始化缓存管理器，使用全局字典作为存储
response_cache_manager = ResponseCacheManager(
    expiry_time=settings.CACHE_EXPIRY_TIME,
    max_entries=settings.MAX_CACHE_ENTRIES,
    cache_dict=response_cache
)

# 活跃请求池 - 将作为活跃请求管理器的内部存储
active_requests_pool = {}

# 初始化活跃请求管理器
active_requests_manager = ActiveRequestsManager(requests_pool=active_requests_pool)

SKIP_CHECK_API_KEY = os.environ.get("SKIP_CHECK_API_KEY", "").lower() == "true"

# --------------- 工具函数 ---------------
# @app.middleware("http")
# async def log_requests(request: Request, call_next):
#     """
#     DEBUG用，接收并打印请求内容
#     """
#     log('info', f"接收到请求: {request.method} {request.url}")
#     try:
#         body = await request.json()
#         log('info', f"请求体: {body}")
#     except Exception:
#         log('info', "请求体不是 JSON 格式或者为空")
    
#     response = await call_next(request)
#     return response

async def check_remaining_keys_async(keys_to_check: list, initial_invalid_keys: list):
    """
    在后台异步检查剩余的 API 密钥。
    """
    local_invalid_keys = []
    found_valid_keys =False

    log('info', f" 开始在后台检查剩余 API Key 是否有效")
    for key in keys_to_check:
        is_valid = await test_api_key(key)
        if is_valid:
            if key not in key_manager.api_keys: # 避免重复添加
                key_manager.api_keys.append(key)
                found_valid_keys = True
            # log('info', f"API Key {key[:8]}... 有效")
        else:
            local_invalid_keys.append(key)
            log('warning', f" API Key {key[:8]}... 无效")
        
        await asyncio.sleep(0.05) # 短暂休眠，避免请求过于密集

    if found_valid_keys:
        key_manager._reset_key_stack() # 如果找到新的有效key，重置栈

    # 合并所有无效密钥 (初始无效 + 后台检查出的无效)
    combined_invalid_keys = list(set(initial_invalid_keys + local_invalid_keys))

    # 获取当前设置中的无效密钥
    current_invalid_keys_str = settings.INVALID_API_KEYS or ""
    current_invalid_keys_set = set(k.strip() for k in current_invalid_keys_str.split(',') if k.strip())

    # 更新无效密钥集合
    new_invalid_keys_set = current_invalid_keys_set.union(set(combined_invalid_keys))

    # 只有当无效密钥列表发生变化时才保存
    if new_invalid_keys_set != current_invalid_keys_set:
        settings.INVALID_API_KEYS = ','.join(sorted(list(new_invalid_keys_set)))
        save_settings()

    log('info', f"密钥检查任务完成。当前总可用密钥数量: {len(key_manager.api_keys)}")

# 设置全局异常处理
sys.excepthook = handle_exception

# --------------- 事件处理 ---------------

@app.on_event("startup")
async def startup_event():
    
    # 初始化CredentialManager
    credential_manager_instance = CredentialManager()
    # 添加到应用程序状态
    app.state.credential_manager = credential_manager_instance
    
    # 初始化Vertex AI服务
    await init_vertex_ai(credential_manager=credential_manager_instance)
    schedule_cache_cleanup(response_cache_manager, active_requests_manager)
    # 检查版本
    await check_version()
    load_settings()
    
    # 密钥检查 
    initial_keys = key_manager.api_keys.copy()
    key_manager.api_keys = [] # 清空，等待检查结果
    first_valid_key = None
    initial_invalid_keys = []
    keys_to_check_later = []

    # 阻塞式查找第一个有效密钥
    for index, key in enumerate(initial_keys):
        is_valid = await test_api_key(key)
        if is_valid:
            log('info', f"找到第一个有效密钥: {key[:8]}...")
            first_valid_key = key
            key_manager.api_keys.append(key) # 添加到管理器
            key_manager._reset_key_stack()
            # 将剩余的key放入后台检查列表
            keys_to_check_later = initial_keys[index + 1:]
            break # 找到即停止
        else:
            log('warning', f"密钥 {key[:8]}... 无效")
            initial_invalid_keys.append(key)
    
    if not first_valid_key:
        log('error', "启动时未能找到任何有效 API 密钥！")
        keys_to_check_later = [] # 没有有效key，无需后台检查
    else:
        # 使用第一个有效密钥加载模型
        try:
            all_models = await GeminiClient.list_available_models(first_valid_key)
            GeminiClient.AVAILABLE_MODELS = [model.replace("models/", "") for model in all_models]
            log('info', f"使用密钥 {first_valid_key[:8]}... 加载可用模型成功")
        except Exception as e:
            log('warning', f"使用密钥 {first_valid_key[:8]}... 加载可用模型失败",extra={'error_message': str(e)})

    if not SKIP_CHECK_API_KEY:
        # 创建后台任务检查剩余密钥
        if keys_to_check_later:
            asyncio.create_task(check_remaining_keys_async(keys_to_check_later, initial_invalid_keys))
        else:
            # 如果没有需要后台检查的key，也要处理初始无效key
            current_invalid_keys_str = settings.INVALID_API_KEYS or ""
            current_invalid_keys_set = set(k.strip() for k in current_invalid_keys_str.split(',') if k.strip())
            new_invalid_keys_set = current_invalid_keys_set.union(set(initial_invalid_keys))
            if new_invalid_keys_set != current_invalid_keys_set:
                 settings.INVALID_API_KEYS = ','.join(sorted(list(new_invalid_keys_set)))
                 save_settings()
                 log('info', f"更新初始无效密钥列表完成，总无效密钥数: {len(new_invalid_keys_set)}")

    else: # 跳过检查
        log('info',"跳过 API 密钥检查")
        key_manager.api_keys.extend(keys_to_check_later)
        key_manager._reset_key_stack()

    # 初始化路由器
    init_router(
        key_manager,
        response_cache_manager,
        active_requests_manager,
        SAFETY_SETTINGS,
        SAFETY_SETTINGS_G2,
        first_valid_key,
        settings.FAKE_STREAMING,
        settings.FAKE_STREAMING_INTERVAL,
        settings.PASSWORD,
        settings.MAX_REQUESTS_PER_MINUTE,
        settings.MAX_REQUESTS_PER_DAY_PER_IP
    )
        
    # 初始化仪表盘路由器
    init_dashboard_router(
        key_manager,
        response_cache_manager,
        active_requests_manager,
        credential_manager_instance
    )

# --------------- 异常处理 ---------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from app.utils import translate_error
    error_message = translate_error(str(exc))
    extra_log_unhandled_exception = {'status_code': 500, 'error_message': error_message}
    log('error', f"Unhandled exception: {error_message}", extra=extra_log_unhandled_exception)
    return JSONResponse(status_code=500, content=ErrorResponse(message=str(exc), type="internal_error").dict())

# --------------- 路由 ---------------

app.include_router(router)
app.include_router(dashboard_router)

# 挂载静态文件目录
app.mount("/assets", StaticFiles(directory="app/templates/assets"), name="assets")

# 设置根路由路径
dashboard_path = f"/{settings.DASHBOARD_URL}" if settings.DASHBOARD_URL else "/"

@app.get(dashboard_path, response_class=HTMLResponse)
async def root(request: Request):
    """
    根路由 - 返回静态 HTML 文件
    """
    base_url = str(request.base_url).replace("http", "https")
    api_url = f"{base_url}v1" if base_url.endswith("/") else f"{base_url}/v1"
    # 直接返回 index.html 文件
    return templates.TemplateResponse(
        "index.html", {"request": request, "api_url": api_url}
    )
