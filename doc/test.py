import openai

client = openai.OpenAI(
  api_key="sk-pPu8NXQEM6v3eoVz8aB8Be7588784058Ac28D7A3E30f227b",  # 换成你在 AiHubMix 生成的密钥
  base_url="https://aihubmix.com/v1"
)

response = client.chat.completions.create(
  model="gemini-2.0-flash",
  messages=[
      {"role": "user", "content": "生命的意义是什么？"}
  ]
)

print(response.choices[0].message.content) # 该模型默认开启思考模式