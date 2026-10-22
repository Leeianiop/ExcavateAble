/*
 * Excavatable - ESP32-C3 Super Mini robot firmware
 * ===============================================
 *
 * Talks to the SnakeBot backend (backend/app/robot/controller.py) over the
 * Nordic UART Service, so the website's Robot Control page can drive servos,
 * the LED ring and read sensors.
 *
 * Arduino IDE settings:
 *   Board:            "ESP32C3 Dev Module"
 *   USB CDC On Boot:  Enabled
 *   Libraries:        ESP32Servo, DHT sensor library, Adafruit Unified Sensor,
 *                     Adafruit NeoPixel
 *
 * Pin map - change these if your wiring differs:
 *   GPIO0   servo 0  A0 / "P" pair (pan)      GPIO6   MQ-8 DO  (digital)
 *   GPIO1   servo 1  A1 / "P" pair (tilt)     GPIO7   MQ-5 DO  (digital)
 *   GPIO3   servo 2  A2 / "Q" pair (lift)     GPIO10  WS2812 data
 *   GPIO4   servo 3  A3 / "Q" pair (lift)     GPIO5   DHT11 data (digital)
 *
 * Command grammar (must match is_valid_command() in the backend):
 *   F B L R S          hold a direction to nudge pan/tilt, S to stop
 *   U D                lift / lower the "Q" pair
 *   A<0..3>:<0..180>   move a single servo channel
 *   P:<0..180>         move both "P" channels (0 and 1)
 *   Q:<0..180>         move both "Q" channels (2 and 3)
 *   RAINBOW_ON / RAINBOW_OFF
 *
 * Telemetry pushed on TX every TELEMETRY_INTERVAL_MS:
 *   "T:27.0C H:61.0%"   then "MQ8:LOW|HIGH"   then "MQ5:LOW|HIGH"
 *
 * NOTE: the rover's stepper drivers are dead (fried buck converters), so no
 * stepper pins are allocated here. F/B/L/R nudge the pan/tilt pair instead,
 * which keeps the D-pad useful during a demo.
 */

#include <Adafruit_NeoPixel.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <DHT.h>
#include <ESP32Servo.h>

// ------------------------------- config --------------------------------
#define DEVICE_NAME "ESP32C3_Servo"

static const uint8_t NUM_SERVOS = 4;
static const uint8_t SERVO_PINS[NUM_SERVOS] = {0, 1, 3, 4};

static const uint8_t PIN_DHT = 5;  // DHT11 is a DIGITAL sensor - not an ADC pin
#define DHT_TYPE DHT11

static const uint8_t PIN_MQ8 = 6;  // MQ-8 digital output
static const uint8_t PIN_MQ5 = 7;  // MQ-5 digital output

static const uint8_t PIN_LED = 10;  // WS2812 data
static const uint8_t NUM_LEDS = 8;

static const uint32_t TELEMETRY_INTERVAL_MS = 2000;
static const uint32_t DRIVE_WATCHDOG_MS = 1000;  // UI refreshes every 250 ms
static const uint32_t PAD_STEP_MS = 40;          // ramp speed while held
static const int PAD_STEP_DEG = 2;

static const int LIFT_ANGLE = 90;  // "U" - Lift
static const int HOME_ANGLE = 0;   // "D" - Lower

// Nordic UART Service - must match backend/app/robot/controller.py
#define SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define RX_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define TX_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// ------------------------------ globals --------------------------------
Servo servos[NUM_SERVOS];
DHT dht(PIN_DHT, DHT_TYPE);
Adafruit_NeoPixel strip(NUM_LEDS, PIN_LED, NEO_GRB + NEO_KHZ800);

BLECharacteristic *txChar = nullptr;

bool deviceConnected = false;
int angles[NUM_SERVOS] = {90, 90, 90, 90};

volatile char activeDir = 0;
volatile uint32_t lastDirMs = 0;

uint32_t lastStepMs = 0;
uint32_t lastTelemetryMs = 0;

bool rainbowOn = false;
uint16_t rainbowHue = 0;

// ------------------------------ servo --------------------------------
void setChannel(uint8_t channel, int angle) {
  if (channel >= NUM_SERVOS) {
    return;
  }
  angle = constrain(angle, 0, 180);
  if (angle == angles[channel]) {
    return;
  }
  angles[channel] = angle;
  servos[channel].write(angle);
}

void setPair(uint8_t a, uint8_t b, int angle) {
  setChannel(a, angle);
  setChannel(b, angle);
}

// ------------------------------ telemetry ------------------------------
void notifyLine(const String &line) {
  if (!deviceConnected || txChar == nullptr) {
    return;
  }
  txChar->setValue((uint8_t *)line.c_str(), line.length());
  txChar->notify();
  delay(30);  // let the BLE stack flush before the next notification
}

void sendTelemetry() {
  if (!deviceConnected || txChar == nullptr) {
    return;
  }

  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();
  if (!isnan(temperature) && !isnan(humidity)) {
    char buffer[48];
    snprintf(buffer, sizeof(buffer), "T:%.1fC H:%.1f%%", temperature, humidity);
    notifyLine(String(buffer));
  }

  // Both gas modules expose a digital output that goes LOW when triggered.
  notifyLine(String("MQ8:") + (digitalRead(PIN_MQ8) == LOW ? "LOW" : "HIGH"));
  notifyLine(String("MQ5:") + (digitalRead(PIN_MQ5) == LOW ? "LOW" : "HIGH"));
}

// ------------------------------ commands -------------------------------
void handleCommand(const String &cmd) {
  if (cmd == "RAINBOW_ON") {
    rainbowOn = true;
    return;
  }
  if (cmd == "RAINBOW_OFF") {
    rainbowOn = false;
    strip.clear();
    strip.show();
    return;
  }

  if (cmd == "U") {
    setPair(2, 3, LIFT_ANGLE);
    return;
  }
  if (cmd == "D") {
    setPair(2, 3, HOME_ANGLE);
    return;
  }

  if (cmd.length() == 1) {
    char letter = cmd[0];
    if (letter == 'F' || letter == 'B' || letter == 'L' || letter == 'R') {
      activeDir = letter;
      lastDirMs = millis();
      return;
    }
    if (letter == 'S') {
      activeDir = 0;
      return;
    }
    return;
  }

  int colon = cmd.indexOf(':');
  if (colon < 0) {
    return;
  }

  String head = cmd.substring(0, colon);
  int value = cmd.substring(colon + 1).toInt();

  if (head == "P") {
    setPair(0, 1, value);
    return;
  }
  if (head == "Q") {
    setPair(2, 3, value);
    return;
  }
  if (head.length() == 2 && head[0] == 'A') {
    setChannel((uint8_t)head.substring(1).toInt(), value);
  }
}

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    // .c_str() keeps this compiling on both ESP32 core 2.x (std::string) and
    // 3.x (String).
    String cmd = String(characteristic->getValue().c_str());
    cmd.trim();
    if (cmd.length() > 0) {
      Serial.print("[rx] ");
      Serial.println(cmd);
      handleCommand(cmd);
    }
  }
};

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    deviceConnected = true;
    Serial.println("[ble] central connected");
  }

  void onDisconnect(BLEServer *server) override {
    deviceConnected = false;
    activeDir = 0;
    // Without this the peripheral stays invisible forever once the first
    // client disconnects - the bug that made the board vanish from scans.
    server->startAdvertising();
    Serial.println("[ble] central disconnected - advertising again");
  }
};

// -------------------------------- drive --------------------------------
// The steppers are dead, so a "drive" command ramps the pan/tilt pair while
// it is held. Replace this function if the wheels ever come back to life.
void stepDrive() {
  if (activeDir == 0) {
    return;
  }

  // The website refreshes the held direction every 250 ms. If those refreshes
  // stop - link dropped, tab closed - stop moving.
  if (millis() - lastDirMs > DRIVE_WATCHDOG_MS) {
    activeDir = 0;
    return;
  }

  if (millis() - lastStepMs < PAD_STEP_MS) {
    return;
  }
  lastStepMs = millis();

  switch (activeDir) {
    case 'L':
      setChannel(0, angles[0] - PAD_STEP_DEG);
      break;
    case 'R':
      setChannel(0, angles[0] + PAD_STEP_DEG);
      break;
    case 'F':
      setChannel(1, angles[1] + PAD_STEP_DEG);
      break;
    case 'B':
      setChannel(1, angles[1] - PAD_STEP_DEG);
      break;
    default:
      break;
  }
}

// ------------------------------- rainbow -------------------------------
void updateRainbow() {
  if (!rainbowOn) {
    return;
  }

  static uint32_t lastFrameMs = 0;
  if (millis() - lastFrameMs < 40) {
    return;
  }
  lastFrameMs = millis();

  for (uint16_t i = 0; i < NUM_LEDS; i++) {
    uint16_t hue = rainbowHue + (i * 65536L / NUM_LEDS);
    strip.setPixelColor(i, strip.gamma32(strip.ColorHSV(hue)));
  }
  strip.show();
  rainbowHue += 256;
}

// -------------------------------- setup --------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[bot] Excavatable ESP32-C3 robot starting");

  pinMode(PIN_MQ8, INPUT);
  pinMode(PIN_MQ5, INPUT);

  dht.begin();

  strip.begin();
  strip.clear();
  strip.show();

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    servos[i].setPeriodHertz(50);
    servos[i].attach(SERVO_PINS[i], 500, 2400);
    servos[i].write(angles[i]);
  }

  BLEDevice::init(DEVICE_NAME);
  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService *service = server->createService(SERVICE_UUID);

  txChar = service->createCharacteristic(TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  // The CCCD descriptor is what lets a central subscribe. Its absence is why
  // the previous sketch could be connected to but never sent any telemetry.
  txChar->addDescriptor(new BLE2902());

  BLECharacteristic *rxChar = service->createCharacteristic(
      RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rxChar->setCallbacks(new RxCallbacks());

  service->start();

  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.print("[ble] advertising as ");
  Serial.println(DEVICE_NAME);
}

// --------------------------------- loop --------------------------------
void loop() {
  stepDrive();
  updateRainbow();

  if (millis() - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryMs = millis();
    sendTelemetry();
  }
}
