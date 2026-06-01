#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>

// [설정 1] 사용하는 와이파이 이름과 비밀번호
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// [설정 2] 홈서버(파이썬)가 실행 중인 PC의 내부 IP 주소 및 포트
// 주의: http:// 는 빼고 적습니다. (예: "192.168.0.10:5000")
String serverIP = "192.168.0.x:5000"; 

// 8번 핀을 제어 핀(내장 LED 등)으로 설정
const int LED_PIN = 8;

// ESP32 자체 웹서버를 80번 포트로 개방
WebServer server(80);

// 현재 스위치 상태 저장
String current_status = "OFF";

// 하트비트(생존 신고) 타이머용 변수
unsigned long lastHeartbeat = 0;
const unsigned long HEARTBEAT_INTERVAL = 120000; // 120초 (2분)

// 함수 선언
void handleServerCommand();
void sendToServer(String endpoint, String triggerInfo);

void setup() {
  Serial.begin(115200);

  // 1. LED 핀 초기화
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); // 시작할 때는 OFF 상태

  // 2. 와이파이 연결
  Serial.print("\nConnecting to WiFi");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi Connected!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP()); 
  // ↑ 이 IP 주소를 파이썬 코드의 ESP32_IP 변수에 적어주면 됩니다.

  // 3. 서버로부터 명령을 받을 주소(/api/command) 라우팅
  server.on("/api/command", HTTP_POST, handleServerCommand);
  
  // 4. ESP32 서버 시작
  server.begin();
  Serial.println("ESP32 Server Started.");
}

void loop() {
  // 웹 요청이 들어오는지 계속 확인
  server.handleClient();

  // 주기적 하트비트 전송 (2분마다 서버에 생존 신고)
  if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL) {
    lastHeartbeat = millis();
    sendToServer("/api/state", "heartbeat");
  }
}

// ==========================================
// [수신] 홈서버 -> ESP32 명령 수신 시 실행
// ==========================================
void handleServerCommand() {
  if (!server.hasArg("plain")) {
    server.send(400, "text/plain", "Bad Request");
    return;
  }
  
  String payload = server.arg("plain");
  Serial.println("\n[수신] 서버 명령 도착: " + payload);

  // JSON 데이터 파싱
  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, payload);

  if (error) {
    Serial.println("JSON 파싱 에러");
    server.send(400, "application/json", "{\"status\":\"error\"}");
    return;
  }

  // 명령 확인 (ON / OFF)
  String cmd = doc["command"]; 
  
  // 8번 핀 조작 및 상태 변경
  if (cmd == "ON") {
    current_status = "ON";
    digitalWrite(LED_PIN, HIGH);
    Serial.println("💡 8번 핀 ON");
  } else if (cmd == "OFF") {
    current_status = "OFF";
    digitalWrite(LED_PIN, LOW);
    Serial.println("⚫ 8번 핀 OFF");
  }

  // 1. "명령 잘 받았음!" 이라고 파이썬 서버에 응답
  server.send(200, "application/json", "{\"result\":\"success\"}");
  
  // 2. 상태가 변했으므로 긴급 보고용 주소로 전송
  sendToServer("/api/change", "web_command");
}

// ==========================================
// [발신] ESP32 -> 홈서버 상태 보고
// ==========================================
void sendToServer(String endpoint, String triggerInfo) {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    // 목적지 URL 만들기 (예: http://192.168.0.10:5000/api/change)
    String url = "http://" + serverIP + endpoint;
    
    http.begin(url);
    http.addHeader("Content-Type", "application/json");

    // 보낼 JSON 데이터 조립
    StaticJsonDocument<256> doc;
    doc["request_time"] = "ESP32"; // 시간 검증은 어차피 파이썬이 하므로 더미 데이터 삽입
    doc["status"] = current_status;
    doc["device_id"] = "switch_01";
    doc["trigger"] = triggerInfo;

    String jsonOutput;
    serializeJson(doc, jsonOutput);

    // POST 전송
    int httpResponseCode = http.POST(jsonOutput);
    
    if (httpResponseCode > 0) {
      Serial.print("[발신] " + endpoint + " 성공 (코드: " + String(httpResponseCode) + ")\n");
    } else {
      Serial.print("[발신] " + endpoint + " 실패: " + http.errorToString(httpResponseCode) + "\n");
    }
    
    http.end();
  } else {
    Serial.println("WiFi 연결 끊김");
  }
}