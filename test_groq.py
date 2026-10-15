
import os
from dotenv import load_dotenv
from groq import Groq

# Load secrets and settings from .env
load_dotenv()

api_key = os.getenv("GROQ_API_KEY")
model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY not found. Check your .env file."
    )

client = Groq(api_key=api_key)

response = client.chat.completions.create(
    model=model,
    messages=[
        {
            "role": "user",
            "content": (
                "Reply with exactly: "
                "Groq connection successful!"
            ),
        }
    ],
)

print("Model:", model)
print("Response:", response.choices[0].message.content)
