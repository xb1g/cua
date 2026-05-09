import os
from openai import OpenAI
client = OpenAI(api_key="sk-cp-VCDOeZW-OXFxMGlGKTaMoiZNlmpLR61deFEpNMnm_7HoctaMW8NgiBkZ3CPK6QBuPG94wB4jT02FVLtmyi2uv85sZU9NdJRvIyR7eJ0S8tVDlfUzk8vCdyk", base_url="https://api.minimax.chat/v1")
try:
    print("Testing chat")
    res = client.chat.completions.create(model="MiniMax-M2.7-highspeed", messages=[{"role": "user", "content": "hi"}], max_tokens=10)
    print(res.choices[0].message.content)
except Exception as e:
    print("Error:", e)
