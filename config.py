from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 管理后台密码
    admin_password: str = "admin123"

    # ========== 国内大模型（直接可用） ==========
    # DeepSeek: platform.deepseek.com 注册即送 500 万 token
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # 通义千问: dashscope.aliyun.com 阿里云账号开通
    qwen_api_key: str = ""
    qwen_api_secret: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 智谱 GLM: open.bigmodel.cn 注册送免费额度
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # 月之暗面 Kimi: platform.moonshot.cn
    moonshot_api_key: str = ""
    moonshot_base_url: str = "https://api.moonshot.cn/v1"

    # 国外模型中转
    transit_api_key: str = ""
    transit_base_url: str = ""
    gpt4o_input_price: float = 0.050
    gpt4o_output_price: float = 0.150
    claude_input_price: float = 0.060
    claude_output_price: float = 0.200

    # xorpay 支付
    xorpay_aid: str = ""
    xorpay_app_secret: str = ""
    xorpay_notify_url: str = "http://localhost:8000/pay/callback"

    # 服务器
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
