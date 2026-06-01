from flask import Flask, request, jsonify, send_file
import datetime
import requests
import os
import subprocess
import re

app = Flask(__name__)

# [설정값]
SECRET_PASSWORD = "password" 
ESP32_MAC = "8c:fd:49:55:00:8c"  # ESP32의 MAC 주소

# 서버 메모리에 저장되는 기기 상태 정보
device_info = {
    "last_seen": "None",
    "current_state": "UNKNOWN",
    "current_ip": None 
}

# MAC 주소를 기반으로 현재 네트워크의 IP를 찾아내는 함수
def find_ip_by_mac(target_mac):
    target_mac_colon = target_mac.lower().replace('-', ':') 
    target_mac_dash = target_mac.lower().replace(':', '-')  
    
    try:
        output = subprocess.check_output('arp -a', shell=True).decode('utf-8', errors='ignore')
        for line in output.split('\n'):
            line_lower = line.lower()
            if target_mac_colon in line_lower or target_mac_dash in line_lower:
                ip_match = re.search(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', line)
                if ip_match:
                    return f"http://{ip_match.group(0)}"
    except Exception as e:
        print(f"ARP 테이블 조회 중 오류 발생: {e}")
        
    return None

# [수정됨] 공통 함수: 해시를 제거하고 단순 비밀번호 비교로 변경
def verify_auth(req_time_str, received_password):
    try:
        req_time_obj = datetime.datetime.strptime(req_time_str, "%Y-%m-%d-%H:%M")
        now = datetime.datetime.now()
        if abs((now - req_time_obj).total_seconds()) > 120:
            return False, "요청 시간이 만료되었습니다."

        # 프론트엔드에서 보낸 비밀번호와 서버의 비밀번호를 직접 비교
        if received_password == SECRET_PASSWORD:
            return True, "Success"
        return False, "비밀번호가 일치하지 않습니다."
    except:
        return False, "잘못된 데이터 형식입니다."

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

@app.route('/api/get_status', methods=['POST'])
def get_secure_status():
    data = request.get_json(silent=True) or {}
    # 'hash' 대신 'password' 키를 받도록 수정 (프론트엔드 코드와 맞춰주세요)
    is_valid, msg = verify_auth(data.get('request_time'), data.get('password'))
    
    if is_valid:
        return jsonify({
            "status": "success", 
            "current_state": device_info["current_state"],
            "esp32_ip": device_info["current_ip"]
        }), 200
    return jsonify({"status": "error", "message": msg}), 403

@app.route('/api/control', methods=['POST'])
def control_switch():
    data = request.get_json(silent=True) or {}
    is_valid, msg = verify_auth(data.get('request_time'), data.get('password'))

    if is_valid:
        target_ip = find_ip_by_mac(ESP32_MAC)
        
        if not target_ip:
            target_ip = device_info.get("current_ip")
            
        if not target_ip:
            return jsonify({"status": "error", "message": "네트워크에서 ESP32 기기를 찾을 수 없습니다."}), 404

        device_info["current_ip"] = target_ip
        command = data.get('command')
        esp_payload = {
            "request_time": datetime.datetime.now().strftime("%Y-%m-%d-%H:%M"),
            "command": command,
            "device_id": data.get('device_id', 'unknown'),
            "sender": "home_server"
        }
        
        try:
            resp = requests.post(f"{target_ip}/api/command", json=esp_payload, timeout=3)
            if resp.status_code == 200:
                return jsonify({"status": "success", "message": f"{command} 전송 완료 (IP: {target_ip})"}), 200
        except Exception as e:
            return jsonify({"status": "error", "message": f"ESP32 연결 실패 ({target_ip})"}), 500
            
    return jsonify({"status": "error", "message": msg}), 403

@app.route('/api/change', methods=['POST'])
def handle_change():
    # 빈 데이터로 인한 서버 다운 방지
    data = request.get_json(silent=True)
    if not data:
        return "Bad Request", 400
        
    device_info["current_state"] = data.get('status', 'UNKNOWN')
    device_info["last_seen"] = data.get('request_time', 'Unknown Time')
    device_info["current_ip"] = f"http://{request.remote_addr}" 
    
    print(f"[{device_info['last_seen']}] 상태 변경: {device_info['current_state']} (IP: {device_info['current_ip']})")
    return "OK", 200

@app.route('/api/state', methods=['POST'])
def handle_state():
    data = request.get_json(silent=True)
    if not data:
        return "Bad Request", 400
        
    device_info["last_seen"] = data.get('request_time', 'Unknown Time')
    device_info["current_state"] = data.get('status', 'UNKNOWN')
    device_info["current_ip"] = f"http://{request.remote_addr}" 
    
    return "ALIVE", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)