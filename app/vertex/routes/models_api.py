import time
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Dict, Any, Optional
from app.vertex.auth import get_api_key, validate_api_key
from app.vertex.model_loader import get_vertex_models, get_vertex_express_models, refresh_models_config_cache
import app.vertex.config as app_config
from app.vertex.credentials_manager import CredentialManager
from app.utils.logging import vertex_log

router = APIRouter(prefix="/models")

# Setup security
security = HTTPBearer()

@router.get("/list")
async def list_models(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    List available models for Vertex generation.
    
    Returns a list of models in OpenAI-compatible format.
    """
    # Validate API key
    api_key = validate_api_key(credentials)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Get available models
    vertex_log('info', "Retrieving list of available models")
    standard_models = await get_vertex_models()
    express_models = await get_vertex_express_models()
    
    # Combine and format
    all_models = []
    
    # Format standard models
    for model_name in standard_models:
        all_models.append({
            "id": model_name,
            "object": "model",
            "created": 1677610602,  # Placeholder timestamp
            "owned_by": "google",
            "permission": [],
            "root": model_name,
            "parent": None,
            "pricing": {
                "prompt": "0.0020",  # Placeholder pricing
                "completion": "0.0020"  # Placeholder pricing
            }
        })
    
    # Format express models
    for model_name in express_models:
        all_models.append({
            "id": model_name,
            "object": "model",
            "created": 1677610602,  # Placeholder timestamp
            "owned_by": "google-express",
            "permission": [],
            "root": model_name,
            "parent": None,
            "pricing": {
                "prompt": "0.0010",  # Lower placeholder pricing for express
                "completion": "0.0010"  # Lower placeholder pricing for express
            }
        })
    
    # Add encryption variants if available
    for base_model in standard_models:
        encrypt_model_name = f"{base_model}-encrypt-full"
        all_models.append({
            "id": encrypt_model_name,
            "object": "model",
            "created": 1677610602,  # Placeholder timestamp
            "owned_by": "google-secure",
            "permission": [],
            "root": base_model,
            "parent": base_model,
            "pricing": {
                "prompt": "0.0025",  # Higher placeholder pricing for encrypt
                "completion": "0.0025"  # Higher placeholder pricing for encrypt
            }
        })
    
    vertex_log('info', f"Found {len(all_models)} available models")
    
    return {
        "object": "list",
        "data": all_models
    }

@router.post("/refresh")
async def refresh_models(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Refresh the models configuration cache.
    
    Forces a refresh of the models available from the configuration source.
    """
    # Validate API key
    api_key = validate_api_key(credentials)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    vertex_log('info', "Attempting to refresh models configuration")
    
    # Attempt refresh
    success = await refresh_models_config_cache()
    
    if success:
        # Get updated counts
        standard_models = await get_vertex_models()
        express_models = await get_vertex_express_models()
        
        vertex_log('info', f"Models refresh successful. Standard: {len(standard_models)}, Express: {len(express_models)}")
        
        return {
            "success": True,
            "message": "Models configuration refreshed successfully",
            "models_count": {
                "standard": len(standard_models),
                "express": len(express_models),
                "total": len(standard_models) + len(express_models)
            }
        }
    else:
        vertex_log('error', "Failed to refresh models configuration")
        
        raise HTTPException(
            status_code=500,
            detail="Failed to refresh models configuration"
        )

@router.get("/v1/models")
async def list_models(fastapi_request: Request, api_key: str = Depends(get_api_key)):
    await refresh_models_config_cache()
    
    OPENAI_DIRECT_SUFFIX = "-openai"
    EXPERIMENTAL_MARKER = "-exp-"
    PAY_PREFIX = "[PAY]"
    
    # 获取credential_manager，如果不存在则创建一个新的
    try:
        credential_manager_instance = fastapi_request.app.state.credential_manager
        vertex_log('info', "Using existing credential manager from app state")
    except AttributeError:
        # 如果app.state中没有credential_manager，则创建一个新的
        vertex_log('warning', "No credential_manager found in app.state, creating a new one")
        credential_manager_instance = CredentialManager()

    # 检查是否有SA凭证和Express Key
    has_sa_creds = credential_manager_instance.get_total_credentials() > 0
    has_express_key = bool(app_config.VERTEX_EXPRESS_API_KEY_VAL)

    raw_vertex_models = await get_vertex_models()
    raw_express_models = await get_vertex_express_models()
    
    candidate_model_ids = set()

    if has_express_key:
        candidate_model_ids.update(raw_express_models)
        # If *only* express key is available, only express models (and their variants) should be listed.
        # The current `vertex_model_ids` from remote config might contain non-express models.
        # The `get_vertex_express_models()` should be the source of truth for express-eligible base models.
        if not has_sa_creds:
            # Only list models that are explicitly in the express list.
            # Suffix generation will apply only to these if they are not gemini-2.0
            all_model_ids = set(raw_express_models)
        else:
            # Both SA and Express are available, combine all known models
            all_model_ids = set(raw_vertex_models + raw_express_models)
    elif has_sa_creds:
        # Only SA creds available, use all vertex_models (which might include express-eligible ones)
        all_model_ids = set(raw_vertex_models)
    else:
        # No credentials available
        all_model_ids = set()
    
    # Create extended model list with variations (search, encrypt, auto etc.)
    # This logic might need to be more sophisticated based on actual supported features per base model.
    # For now, let's assume for each base model, we might have these variations.
    # A better approach would be if the remote config specified these variations.
    
    dynamic_models_data: List[Dict[str, Any]] = []
    current_time = int(time.time())

    # Add base models and their variations
    for original_model_id in sorted(list(all_model_ids)):
        current_display_prefix = ""
        # Only add PAY_PREFIX if the model is not already an EXPRESS model (which has its own prefix)
        if not original_model_id.startswith("[EXPRESS]") and \
           has_sa_creds and not has_express_key and EXPERIMENTAL_MARKER not in original_model_id:
            current_display_prefix = PAY_PREFIX
        
        base_display_id = f"{current_display_prefix}{original_model_id}"
        
        dynamic_models_data.append({
            "id": base_display_id, "object": "model", "created": current_time, "owned_by": "google",
            "permission": [], "root": original_model_id, "parent": None
        })
        
        # Conditionally add common variations (standard suffixes)
        if not original_model_id.startswith("gemini-2.0"): # Suffix rules based on original_model_id
            standard_suffixes = ["-search", "-encrypt", "-encrypt-full", "-auto"]
            for suffix in standard_suffixes:
                # Suffix is applied to the original model ID part
                suffixed_model_part = f"{original_model_id}{suffix}"
                # Then the whole thing is prefixed
                final_suffixed_display_id = f"{current_display_prefix}{suffixed_model_part}"
                
                # Check if this suffixed ID is already in all_model_ids (unlikely with prefix) or already added
                if final_suffixed_display_id not in all_model_ids and not any(m['id'] == final_suffixed_display_id for m in dynamic_models_data):
                    dynamic_models_data.append({
                        "id": final_suffixed_display_id, "object": "model", "created": current_time, "owned_by": "google",
                        "permission": [], "root": original_model_id, "parent": None
                    })
        
        # Apply special suffixes for models starting with "gemini-2.5-flash"
        if original_model_id.startswith("gemini-2.5-flash"): # Suffix rules based on original_model_id
            special_flash_suffixes = ["-nothinking", "-max"]
            for special_suffix in special_flash_suffixes:
                suffixed_model_part = f"{original_model_id}{special_suffix}"
                final_special_suffixed_display_id = f"{current_display_prefix}{suffixed_model_part}"

                if final_special_suffixed_display_id not in all_model_ids and not any(m['id'] == final_special_suffixed_display_id for m in dynamic_models_data):
                    dynamic_models_data.append({
                        "id": final_special_suffixed_display_id, "object": "model", "created": current_time, "owned_by": "google",
                        "permission": [], "root": original_model_id, "parent": None
                    })

        # Ensure uniqueness again after adding suffixes
        # Add OpenAI direct variations if SA creds are available
        if has_sa_creds: # OpenAI direct mode only works with SA credentials
            # `all_model_ids` contains the comprehensive list of base models that are eligible based on current credentials
            # We iterate through this to determine which ones get an -openai variation.
            # `raw_vertex_models` is used here to ensure we only add -openai suffix to models that are
            # fundamentally Vertex models, not just any model that might appear in `all_model_ids` (e.g. from Express list exclusively)
            # if express only key is provided.
            # We iterate through the base models from the main Vertex list.
            for base_model_id_for_openai in raw_vertex_models: # Iterate through original list of GAIA/Vertex base models
                display_model_id = ""
                if EXPERIMENTAL_MARKER in base_model_id_for_openai:
                    display_model_id = f"{base_model_id_for_openai}{OPENAI_DIRECT_SUFFIX}"
                else:
                    display_model_id = f"{PAY_PREFIX}{base_model_id_for_openai}{OPENAI_DIRECT_SUFFIX}"
                
                # Check if already added (e.g. if remote config somehow already listed it or added as a base model)
                if display_model_id and not any(m['id'] == display_model_id for m in dynamic_models_data):
                    dynamic_models_data.append({
                        "id": display_model_id, "object": "model", "created": current_time, "owned_by": "google",
                        "permission": [], "root": base_model_id_for_openai, "parent": None
                    })
    
    return {"object": "list", "data": sorted(dynamic_models_data, key=lambda x: x['id'])}