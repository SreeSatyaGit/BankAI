from groq import Groq
client = Groq(api_key=grop_api_key)  # get free key at console.groq.com
resp = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[{"role":"user","content":"hello"}]
)
print(resp.choices[0].message.content)
