/*
 * Excavatable - ESP32-C3 Super Mini
 * =================================
 *
 * Minimal RGB LED test. Nothing else is on this board:
 * no BLE, no PCA9685, no servos, no DHT, no MQ sensors.
 *
 * Wiring:
 *   GPIO8 -> WS2812 data in
 *
 * The strip cycles red -> green -> blue on a loop so you can confirm the
 * LED data line and power are good.
 */

#include <Adafruit_NeoPixel.h>

static const uint8_t PIN_LED = 8;
static const uint8_t NUM_LEDS = 8;
static const uint32_t STEP_MS = 500;

Adafruit_NeoPixel strip(NUM_LEDS, PIN_LED, NEO_GRB + NEO_KHZ800);

void fill(uint8_t r, uint8_t g, uint8_t b) {
  for (uint16_t i = 0; i < NUM_LEDS; i++) {
    strip.setPixelColor(i, strip.Color(r, g, b));
  }
  strip.show();
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[led] RGB test starting on GPIO8");

  strip.begin();
  strip.clear();
  strip.show();
}

void loop() {
  fill(255, 0, 0);
  Serial.println("[led] red");
  delay(STEP_MS);

  fill(0, 255, 0);
  Serial.println("[led] green");
  delay(STEP_MS);

  fill(0, 0, 255);
  Serial.println("[led] blue");
  delay(STEP_MS);
}
