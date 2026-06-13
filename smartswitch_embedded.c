#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>

// ================= [네트워크 설정 및 변수] =================
const char* ssid = "ssidssid";          // 본인 와이파이 이름
const char* password = "12345678";  // 본인 와이파이 비밀번호
String flaskServerUrl = "http://192.168.0.1:5000"; // 본인 플라스크 서버 주소

// ================= [핀 설정] =================
#define TOUCH_PIN 20
#define RELAY_PIN 21
#define RED_PIN 5
#define GREEN_PIN 6
#define BLUE_PIN 7

// ================= [전역 변수] =================
WebServer server(80);

volatile bool lightState = false;

// 터치 관련
volatile bool touchTriggered = false;

unsigned long lastTouchTime = 0;
unsigned long lastRelayToggleTime = 0;

// 서버 전송 관련
bool needToSendChange = false;

unsigned long lastHeartbeatTime = 0;
const unsigned long HEARTBEAT_INTERVAL = 30000;

// ================= [비동기 네트워크 Task 변수] =================
volatile bool sendRequestFlag = false;
String pendingEndpoint = "";

// ================= [함수 선언] =================
void setRGB(int r, int g, int b);
void updateStatusLED();
void setLightState(bool state);
void sendStatusToServer(String endpoint);
String getFormattedTime();
void handleServerCommand();
void networkTask(void * parameter);

// ================= [터치 인터럽트] =================
void IRAM_ATTR onTouchISR() {
  touchTriggered = true;
}

// ================= [SETUP] =================
void setup() {

  Serial.begin(115200);

  pinMode(TOUCH_PIN, INPUT);

  pinMode(RELAY_PIN, OUTPUT);

  pinMode(RED_PIN, OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BLUE_PIN, OUTPUT);

  digitalWrite(RELAY_PIN, LOW);

  setRGB(255, 0, 0);

  // 인터럽트 등록
  attachInterrupt(
    digitalPinToInterrupt(TOUCH_PIN),
    onTouchISR,
    RISING
  );

  // ================= WIFI 연결 =================
  Serial.print("WiFi 연결 중...");

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi 연결 완료!");

  Serial.print("IP: ");
  Serial.println(WiFi.localIP());

  // ================= 시간 동기화 =================
  configTime(
    9 * 3600,
    0,
    "pool.ntp.org",
    "time.nist.gov"
  );

  // ================= 웹서버 =================
  server.on(
    "/api/command",
    HTTP_POST,
    handleServerCommand
  );

  server.begin();

  // ================= 네트워크 Task 생성 =================
  xTaskCreatePinnedToCore(
    networkTask,      // 함수
    "NetworkTask",    // 이름
    10000,            // stack size
    NULL,
    1,
    NULL,
    0                 // Core0
  );

  updateStatusLED();

  // 최초 상태 보고
  pendingEndpoint = "/api/change";
  sendRequestFlag = true;
}

// ================= [LOOP] =================
void loop() {

  updateStatusLED();

  // 웹서버 처리
  server.handleClient();

  // ================= 터치 처리 =================
  if (touchTriggered) {
    touchTriggered = false;

    // 100ms 동안 10ms 간격으로 핀 상태를 샘플링합니다.
    int highCount = 0;
    for (int i = 0; i < 10; i++) {
      if (digitalRead(TOUCH_PIN) == HIGH) {
        highCount++;
      }
      delay(10);
    }

    // 100ms 중 최소 80%(8번 이상) 동안 HIGH가 유지되었다면 진짜 사람이 누른 것입니다
    if (highCount > 8) {
      if (millis() - lastTouchTime > 300) { // 터치 간격 디바운스도 500ms로 상향
        lastTouchTime = millis();
        setLightState(!lightState);
        lastRelayToggleTime = millis();
        needToSendChange = true;
      }
    }
  }

  // ================= 상태 변경 보고 =================
  if (
    needToSendChange &&
    millis() - lastRelayToggleTime > 1000
  ) {

    needToSendChange = false;

    pendingEndpoint = "/api/change";
    sendRequestFlag = true;
  }

  // ================= heartbeat =================
  if (
    millis() - lastHeartbeatTime >
    HEARTBEAT_INTERVAL
  ) {

    lastHeartbeatTime = millis();

    if (!needToSendChange) {

      pendingEndpoint = "/api/state";
      sendRequestFlag = true;
    }
  }

  delay(1);
}

// ================= [비동기 네트워크 Task] =================
void networkTask(void * parameter) {

  while (true) {

    if (sendRequestFlag) {

      sendRequestFlag = false;

      if (WiFi.status() == WL_CONNECTED) {

        sendStatusToServer(pendingEndpoint);
      }
    }

    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// ================= [RGB LED] =================
void setRGB(int r, int g, int b) {

  analogWrite(RED_PIN, r);
  analogWrite(GREEN_PIN, g);
  analogWrite(BLUE_PIN, b);
}

// ================= [상태 LED 업데이트] =================
void updateStatusLED() {

  if (WiFi.status() != WL_CONNECTED) {

    setRGB(255, 0, 0);

  } else if (lightState) {

    setRGB(255, 255, 255);

  } else {

    setRGB(0, 0, 255);
  }
}

// ================= [릴레이 제어] =================
void setLightState(bool state) {

  lightState = state;

  digitalWrite(
    RELAY_PIN,
    lightState ? HIGH : LOW
  );

  updateStatusLED();

  Serial.println(
    lightState ? "전등 ON" : "전등 OFF"
  );
}

// ================= [시간 문자열 생성] =================
String getFormattedTime() {

  struct tm timeinfo;

  if (!getLocalTime(&timeinfo)) {

    return "2000-01-01-00:00";
  }

  char timeStringBuff[50];

  strftime(
    timeStringBuff,
    sizeof(timeStringBuff),
    "%Y-%m-%d-%H:%M",
    &timeinfo
  );

  return String(timeStringBuff);
}

// ================= [서버 전송] =================
void sendStatusToServer(String endpoint) {

  WiFiClient client;

  HTTPClient http;

  // timeout 줄이기
  http.setTimeout(500);

  // keep-alive
  http.setReuse(true);

  String fullUrl = flaskServerUrl + endpoint;

  Serial.print("POST: ");
  Serial.println(fullUrl);

  if (!http.begin(client, fullUrl)) {

    Serial.println("HTTP begin 실패");

    return;
  }

  http.addHeader(
    "Content-Type",
    "application/json"
  );

  StaticJsonDocument<200> doc;

  doc["request_time"] = getFormattedTime();

  doc["status"] =
    lightState ? "ON" : "OFF";

  doc["device_id"] =
    "esp32_room_light";

  String requestBody;

  serializeJson(doc, requestBody);

  int httpResponseCode =
    http.POST(requestBody);

  if (httpResponseCode > 0) {

    Serial.printf(
      "전송 성공 (%s): %d\n",
      endpoint.c_str(),
      httpResponseCode
    );

  } else {

    Serial.printf(
      "전송 실패: %s\n",
      http.errorToString(
        httpResponseCode
      ).c_str()
    );
  }

  http.end();
}

// ================= [플라스크 명령 수신] =================
void handleServerCommand() {

  if (!server.hasArg("plain")) {

    server.send(
      400,
      "application/json",
      "{\"status\":\"error\",\"message\":\"Body not found\"}"
    );

    return;
  }

  String body = server.arg("plain");

  StaticJsonDocument<200> doc;

  DeserializationError error =
    deserializeJson(doc, body);

  if (error) {

    server.send(
      400,
      "application/json",
      "{\"status\":\"error\",\"message\":\"JSON parsing failed\"}"
    );

    return;
  }

  String command = doc["command"];

  if (command == "ON" && !lightState) {

    setLightState(true);

  } else if (
    command == "OFF" &&
    lightState
  ) {

    setLightState(false);

  } else if (command == "TOGGLE") {

    setLightState(!lightState);
  }

  server.send(
    200,
    "application/json",
    "{\"status\":\"success\"}"
  );
}