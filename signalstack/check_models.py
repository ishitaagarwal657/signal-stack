import os, requests
for l in open(".env"):
    if l.startswith("GEMINI_API_KEY="):
        key = l.split("=", 1)[1].strip()
r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key})
print(r.status_code)
if r.status_code == 200:
    for m in r.json()["models"]:
        if "generateContent" in m["supportedGenerationMethods"]:
            print(m["name"])
else:
    print(r.text[:400])