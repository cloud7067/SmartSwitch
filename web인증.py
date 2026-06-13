from flask import Flask, request, jsonify
import requests
import ssl
import os

app = Flask(__name__)

# ================= [인증서 및 포트 설정] =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_FILE = os.path.join(BASE_DIR, "server_domain-chain.pem")
KEY_FILE = os.path.join(BASE_DIR, "server_domain-key.pem")
PORT = 443 
# ========================================================

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json or {}
    
    # 삼성 스마트싱스가 보낸 헤더(Authorization) 콘솔에 출력 (보내주신 예시의 console.log 부분)
    print("\n[SmartThings Authorization Header]:", request.headers.get('Authorization'))
    
    # 1. 최초 앱 등록(CONFIRMATION 또는 PING) 처리
    lifecycle = data.get("lifecycle")
    if lifecycle == "CONFIRMATION":
        confirm_url = data.get("confirmationData", {}).get("confirmationUrl")
        if confirm_url:
            print(f"🔗 인증 URL 발견! 삼성으로 전송합니다: {confirm_url}")
            try:
                # 삼성이 준 주소로 토스 (GET 요청)
                res = requests.get(confirm_url, timeout=5)
                print(f"✅ 인증 성공! 삼성 서버 응답 코드: {res.status_code}")
                return jsonify({"status": "OK"}), 200
            except Exception as e:
                print(f"❌ 요청 실패: {e}")
                return "Failed", 500

    # 2. 스마트싱스 SDK가 내부적으로 가볍게 핑을 쳐볼 때 대응
    return jsonify({}), 200

if __name__ == '__main__':
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    
    print(f"🚀 인증 대기 중... URL: /webhook")
    app.run(host='0.0.0.0', port=PORT, ssl_context=context)