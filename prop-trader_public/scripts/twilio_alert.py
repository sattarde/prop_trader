import urllib.request
import urllib.parse
import base64
import os
import sys

def get_twilio_config():
    config = {}
    try:
        with open("/Users/sattarde/.gemini/skills/prop-trader/.env", "r") as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    config[key] = val
    except:
        pass
    return config

def send_whatsapp(message):
    config = get_twilio_config()
    sid = config.get("TWILIO_ACCOUNT_SID")
    token = config.get("TWILIO_AUTH_TOKEN")
    from_no = config.get("TWILIO_WHATSAPP_FROM")
    to_no = config.get("USER_WHATSAPP_TO")

    if not all([sid, token, from_no, to_no]):
        print("❌ Missing Twilio configuration in .env")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    
    data = urllib.parse.urlencode({
        "From": f"whatsapp:{from_no}",
        "To": f"whatsapp:{to_no}",
        "Body": message
    }).encode("ascii")

    auth_str = f"{sid}:{token}"
    auth_b64 = base64.b64encode(auth_str.encode("ascii")).decode("ascii")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {auth_b64}")

    try:
        with urllib.request.urlopen(req) as response:
            res = response.read().decode("utf-8")
            print("✅ WhatsApp Alert Sent Successfully!")
            return True
    except Exception as e:
        print(f"❌ Failed to send WhatsApp: {e}")
        return False

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Prop Trader: Connection Test Successful! 🚀"
    send_whatsapp(msg)
