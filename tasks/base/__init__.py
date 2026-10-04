def get_retry_count(default: int) -> int:
    """读取通用识别重试次数；0 保留当前流程原有次数。"""
    from module.config import cfg

    return cfg.retry_count or default


def update_model_for_retry(remaining: int, normal_at: int = 20, aggressive_at: int = 10):
    """根据剩余重试次数渐进切换 auto.model: clam → normal → aggressive。"""
    from module.automation import auto
    if remaining < aggressive_at:
        auto.model = "aggressive"
    elif remaining < normal_at:
        auto.model = "normal"
