import os
import requests

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

def run_pipeline():
    print("Authenticating with Google via GitHub Secrets...")
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }

    response = requests.post(token_url, data=payload)
    token_data = response.json()

    if "access_token" in token_data:
        access_token = token_data["access_token"]
        print("SUCCESS: GitHub Actions obtained a fresh access token!")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        drive_res = requests.get("https://www.googleapis.com/drive/v3/files?pageSize=5", headers=headers)
        
        if drive_res.status_code == 200:
            print("SUCCESS: Google Drive API connection verified inside GitHub workflow.")
        else:
            print(f"Drive API Error: {drive_res.text}")
    else:
        print("Authentication Error:", token_data)

if __name__ == "__main__":
    run_pipeline()
