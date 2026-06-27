import os
from openai import OpenAI

api_key = os.environ.get("MOONSHOT_API_KEY")
base_url = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
model = os.environ.get("MOONSHOT_MODEL", "kimi-k2.6")

if not api_key:
    raise RuntimeError("Missing MOONSHOT_API_KEY. 请先设置环境变量。")

client = OpenAI(
    api_key=api_key,
    base_url=base_url,
)

completion = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "user", "content": "你好，只回复：Kimi连接成功"}
    ],
)

print(completion.choices[0].message.content)
