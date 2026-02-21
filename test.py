import requests
import json

url = "https://wandbox.org/api/compile.ndjson"

payload = {
    "compiler": "cpython-3.14.0",
    "title": "",
    "description": "",
    "code": "print(1)",
    "codes": [],
    "options": "",
    "compiler-option-raw": "",
    "runtime-option-raw": "",
    "stdin": ""
}

headers = {
    "Content-Type": "application/json"
}

response = requests.post(url, headers=headers, data=json.dumps(payload))

for line in response.text.strip().split("\n"):
    print(line)


    {"type":"Control","data":"Start"}
{"type":"StdOut","data":"1\n"}
{"type":"ExitCode","data":"0"}
{"type":"Control","data":"Finish"}