// ESP32-WROOM32 external safety controller. No Wi-Fi, no transparent servo bridge.
// HARDWARE_VERIFIED must remain zero until the documented bench checks pass.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <atomic>
#include <cmath>
#include "driver/gpio.h"

#ifndef HARDWARE_VERIFIED
#define HARDWARE_VERIFIED 0
#endif

constexpr uint8_t RX_PIN = 16, TX_PIN = 17, POWER_PIN = 25;
constexpr uint8_t ARM_PIN = 26, ESTOP_PIN = 27, SERVICE_PIN = 33;
constexpr uint8_t WATCHDOG_PIN = 32;
constexpr uint8_t SERVO_IDS[] = {1, 2, 3};
constexpr uint32_t HEARTBEAT_MS = 400, TELEMETRY_MS = 250;
constexpr uint32_t MAX_ACTION_MS = 500, ARM_WINDOW_MS = 5000;
constexpr float MAX_MPS = 0.1f, WHEEL_RADIUS_M = 0.0325f;
// Candidate ST-series conversions, REQUIRED physical verification for ST3020.
constexpr float CURRENT_A_PER_COUNT = 0.0065f;
constexpr float STEPS_PER_M = 4096.0f / (2.0f * PI * WHEEL_RADIUS_M);
constexpr int LEFT_SIGN = 1, RIGHT_SIGN = -1;
constexpr uint16_t GRIPPER_OPEN = 1800, GRIPPER_CLOSED = 2200;
constexpr uint16_t GRIPPER_SPEED = 200, WHEEL_MAX_STEPS_S = 2100;
constexpr float MAX_CURRENT_A = 1.5f, MAX_TEMP_C = 55.0f;
constexpr float MIN_VOLTAGE = 9.0f, MAX_VOLTAGE = 12.6f;

HardwareSerial bus(2);
std::atomic<bool> armed{false}, powered{false}, telemetryValid{false};
std::atomic<uint32_t> lastHeartbeat{0}, lastTelemetry{0}, actionUntil{0}, prepUntil{0};
std::atomic<const char *> fault{"startup"};
float temperatures[3] = {}, currents[3] = {}, voltage = 0;
uint32_t lastArmPress = 0, lastSequence = 0;
std::atomic<bool> haveArmPress{false}, armReleaseRequired{true};
char line[769];
size_t lineLength = 0;
bool discardingLine = false;

bool expired(uint32_t now, uint32_t deadline) {
    return int32_t(now - deadline) >= 0; // wrap-safe deadlines, all below 2^31ms.
}

void cutPower(const char *reason) {
    gpio_set_level(gpio_num_t(POWER_PIN), 0); // Cut first, even if servo bus is broken.
    gpio_set_level(gpio_num_t(WATCHDOG_PIN), 0);
    armed.store(false);
    powered.store(false);
    telemetryValid.store(false);
    haveArmPress.store(false);
    armReleaseRequired.store(true);
    actionUntil.store(0);
    prepUntil.store(0);
    fault.store(reason);
}

// Protocol facts checked against Waveshare's SCServo source. Responses have a
// checksum, exact ID and length, and zero servo-error byte. No unchecked writes.
bool transact(uint8_t id, uint8_t instruction, const uint8_t *parameters,
              size_t count, uint8_t *result, size_t resultCount) {
    if (!powered.load() || count > 16 || resultCount > 20) return false;
    while (bus.available()) bus.read();
    uint8_t packet[24] = {0xff, 0xff, id, uint8_t(count + 2), instruction};
    uint8_t sum = id + packet[3] + instruction;
    for (size_t i = 0; i < count; ++i) {
        packet[5 + i] = parameters[i];
        sum += parameters[i];
    }
    packet[5 + count] = uint8_t(~sum);
    bus.write(packet, count + 6);
    bus.flush();
    uint8_t response[26];
    size_t received = 0;
    uint32_t start = millis();
    while (received < resultCount + 6 && millis() - start < 8) {
        if (!powered.load()) return false;
        if (bus.available()) response[received++] = uint8_t(bus.read());
        else delayMicroseconds(50);
    }
    if (received != resultCount + 6 || response[0] != 0xff || response[1] != 0xff ||
        response[2] != id || response[3] != resultCount + 2 || response[4] != 0) return false;
    sum = 0;
    for (size_t i = 2; i < received - 1; ++i) sum += response[i];
    if (uint8_t(~sum) != response[received - 1]) return false;
    if (resultCount) memcpy(result, response + 5, resultCount);
    return true;
}

bool readRegister(uint8_t id, uint8_t address, uint8_t *data, uint8_t count) {
    uint8_t parameters[] = {address, count};
    return transact(id, 2, parameters, sizeof(parameters), data, count);
}

bool writeRegister(uint8_t id, uint8_t address, const uint8_t *data, uint8_t count) {
    if (count > 7) return false;
    uint8_t parameters[8];
    parameters[0] = address;
    memcpy(parameters + 1, data, count);
    return transact(id, 3, parameters, count + 1, nullptr, 0);
}

bool torque(uint8_t id, bool enable) {
    uint8_t data = enable ? 1 : 0;
    return writeRegister(id, 40, &data, 1);
}

bool wheel(uint8_t id, float command, int sign) {
    int steps = int(lroundf(command * MAX_MPS * STEPS_PER_M)) * sign;
    if (abs(steps) > WHEEL_MAX_STEPS_S) return false;
    uint16_t encoded = uint16_t(abs(steps));
    if (steps < 0) encoded |= 0x8000;
    uint8_t data[] = {uint8_t(encoded), uint8_t(encoded >> 8)};
    return writeRegister(id, 46, data, sizeof(data));
}

bool gripper(float value) {
    uint16_t pos = uint16_t(lroundf(GRIPPER_OPEN + value * (GRIPPER_CLOSED - GRIPPER_OPEN)));
    uint8_t data[] = {20, uint8_t(pos), uint8_t(pos >> 8), 0, 0,
                      uint8_t(GRIPPER_SPEED), uint8_t(GRIPPER_SPEED >> 8)};
    return writeRegister(SERVO_IDS[2], 41, data, sizeof(data));
}

bool stopMotion() {
    bool a = wheel(SERVO_IDS[0], 0, LEFT_SIGN);
    bool b = wheel(SERVO_IDS[1], 0, RIGHT_SIGN);
    bool c = torque(SERVO_IDS[2], false); // Bounded gripper actuation ends too.
    if (!(a && b && c)) cutPower("stop acknowledgement missing");
    actionUntil.store(0);
    return a && b && c;
}

bool pollTelemetry() {
    float minimumVoltage = 100;
    for (size_t i = 0; i < 3; ++i) {
        uint8_t mode = 255, data[15];
        if (!readRegister(SERVO_IDS[i], 33, &mode, 1) ||
            mode != (i == 2 ? 0 : 1) ||
            !readRegister(SERVO_IDS[i], 56, data, sizeof(data))) return false;
        // Register 65 is the servo status byte, distinct from packet error byte.
        if (data[9] != 0) return false;
        float v = data[6] * 0.1f;
        temperatures[i] = data[7];
        uint16_t raw = uint16_t(data[13]) | (uint16_t(data[14]) << 8);
        currents[i] = float(raw & 0x7fff) * CURRENT_A_PER_COUNT;
        if (v < MIN_VOLTAGE || v > MAX_VOLTAGE || temperatures[i] > MAX_TEMP_C ||
            currents[i] > MAX_CURRENT_A) return false;
        minimumVoltage = fminf(minimumVoltage, v);
    }
    voltage = minimumVoltage;
    lastTelemetry.store(millis());
    telemetryValid.store(true);
    return true;
}

// Separate task from JSON parsing and servo I/O. Runs even if the host freezes
// or the main loop blocks. Physical E-stop also interrupts the switch directly.
void safetyTask(void *) {
    bool pulse = false;
    uint32_t lastPulse = 0;
    for (;;) {
        uint32_t now = millis();
        if (digitalRead(ESTOP_PIN) != LOW) cutPower("emergency stop open");
        if (digitalRead(SERVICE_PIN) != LOW) cutPower("hardware authorization interlock open");
        if (armed.load()) {
            if (now - lastHeartbeat.load() >= HEARTBEAT_MS) cutPower("host heartbeat expired");
            else if (!telemetryValid.load() || now - lastTelemetry.load() >= TELEMETRY_MS)
                cutPower("servo telemetry stale");
            else {
                uint32_t deadline = actionUntil.load();
                // Main loop has up to 25ms for acknowledged zero-speed writes.
                if (deadline && expired(now, deadline + 25)) cutPower("action deadline overrun");
            }
        } else if (powered.load() && expired(now, prepUntil.load())) {
            cutPower("arm preparation expired");
        }
        if (powered.load() && now - lastPulse >= 20) {
            pulse = !pulse;
            gpio_set_level(gpio_num_t(WATCHDOG_PIN), pulse);
            lastPulse = now;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

void status(JsonObject out) {
    out["armed"] = armed.load();
    out["ready"] = bool(HARDWARE_VERIFIED) && powered.load() && telemetryValid.load() &&
                   millis() - lastTelemetry.load() < TELEMETRY_MS && digitalRead(ESTOP_PIN) == LOW &&
                   digitalRead(SERVICE_PIN) == LOW;
    out["age_ms"] = telemetryValid.load() ? millis() - lastTelemetry.load() : 1000000;
    out["voltage_v"] = voltage;
    out["fault"] = fault.load();
    out["hardware_verified"] = bool(HARDWARE_VERIFIED);
    JsonArray temps = out.createNestedArray("temperatures_c");
    JsonArray amps = out.createNestedArray("currents_a");
    for (size_t i = 0; i < 3; ++i) { temps.add(temperatures[i]); amps.add(currents[i]); }
}

bool finiteNumber(JsonVariant value, float &result) {
    if (value.is<bool>() || value.is<const char *>() || value.isNull() ||
        (!value.is<double>() && !value.is<long>())) return false;
    result = value.as<float>();
    return std::isfinite(result);
}

void command(const char *input) {
    StaticJsonDocument<768> request;
    StaticJsonDocument<768> response;
    auto parsing = deserializeJson(request, input);
    response["seq"] = request["seq"].as<uint32_t>();
    response["ok"] = false;
    const char *error = nullptr;
    const char *op = request["op"].is<const char *>() ? request["op"].as<const char *>() : "";
    uint32_t seq = request["seq"].as<uint32_t>();
    if (parsing || !request["seq"].is<uint32_t>() || !seq) error = "invalid JSON command";
    else if (strcmp(op, "stop") == 0) { cutPower("host stop"); }
    else if (seq <= lastSequence) error = "replayed or out-of-order command";
    else {
        lastSequence = seq;
        if (strcmp(op, "status") == 0) status(response.createNestedObject("telemetry"));
        else if (strcmp(op, "heartbeat") == 0) {
            if (!request["tracking_ok"].is<bool>() || request["tracking_ok"].as<bool>() != true)
                error = "tracking invalid";
            else lastHeartbeat.store(millis());
        } else if (strcmp(op, "arm") == 0) {
            if (!HARDWARE_VERIFIED) error = "hardware verification disabled";
            else if (!haveArmPress || millis() - lastArmPress > ARM_WINDOW_MS) error = "physical arm press required";
            else if (!powered.load() || !telemetryValid.load() || millis() - lastTelemetry.load() >= TELEMETRY_MS)
                error = "servo telemetry unavailable";
            else if (digitalRead(ESTOP_PIN) != LOW) error = "emergency stop open";
            else if (digitalRead(SERVICE_PIN) != LOW) error = "hardware authorization interlock open";
            else {
                haveArmPress = false; // Every rearm consumes a fresh physical press.
                lastHeartbeat.store(millis());
                armed.store(true);
                if (!wheel(1, 0, LEFT_SIGN) || !wheel(2, 0, RIGHT_SIGN) || !torque(1, true) || !torque(2, true))
                    error = "servo arm acknowledgement missing";
                else fault.store("armed");
            }
        } else if (strcmp(op, "action") == 0) {
            float left, right, duration, grip = 0;
            bool hasGrip = !request["gripper"].isNull();
            if (!armed.load()) error = "disarmed";
            else if (!finiteNumber(request["left"], left) || !finiteNumber(request["right"], right) ||
                     !finiteNumber(request["duration_ms"], duration) || fabsf(left) > 1 || fabsf(right) > 1 ||
                     duration < 1 || duration > MAX_ACTION_MS || floorf(duration) != duration ||
                     (hasGrip && (!finiteNumber(request["gripper"], grip) || grip < 0 || grip > 1)))
                error = "action exceeds envelope";
            else {
                actionUntil.store(millis() + uint32_t(duration));
                bool ok = wheel(1, left, LEFT_SIGN) && wheel(2, right, RIGHT_SIGN);
                if (ok && hasGrip) ok = gripper(grip) && torque(3, true);
                if (!ok) error = "servo action acknowledgement missing";
            }
        } else error = "unknown operation";
    }
    if (error) { cutPower(error); response["error"] = error; }
    else response["ok"] = true;
    serializeJson(response, Serial);
    Serial.println();
}

void setup() {
    digitalWrite(POWER_PIN, LOW);
    pinMode(POWER_PIN, OUTPUT);
    digitalWrite(WATCHDOG_PIN, LOW);
    pinMode(WATCHDOG_PIN, OUTPUT);
    pinMode(ARM_PIN, INPUT_PULLUP);
    pinMode(ESTOP_PIN, INPUT_PULLUP);
    pinMode(SERVICE_PIN, INPUT_PULLUP);
    Serial.begin(115200);
    bus.begin(1000000, SERIAL_8N1, RX_PIN, TX_PIN);
    cutPower("startup");
    xTaskCreatePinnedToCore(safetyTask, "motor-safety", 2048, nullptr, 10, nullptr, 0);
}

void loop() {
    static bool armWasDown = false;
    static uint32_t polledAt = 0;
    bool armDown = digitalRead(ARM_PIN) == LOW;
    if (!armDown) armReleaseRequired.store(false);
    if (armDown && !armWasDown && !armReleaseRequired.load() && HARDWARE_VERIFIED && digitalRead(ESTOP_PIN) == LOW &&
        digitalRead(SERVICE_PIN) == LOW && !armed.load()) {
        lastArmPress = millis();
        haveArmPress = true;
        prepUntil.store(millis() + ARM_WINDOW_MS);
        powered.store(true);
        digitalWrite(POWER_PIN, HIGH);
        // Explicit human button authorizes this initial bus energization.
        delay(50);
        bool ok = torque(1, false) && torque(2, false) && torque(3, false);
        if (!ok) cutPower("pre-arm torque-off acknowledgement missing");
    }
    armWasDown = armDown;
    uint32_t now = millis();
    uint32_t deadline = actionUntil.load();
    if (armed.load() && deadline && expired(now, deadline)) stopMotion();
    if (powered.load() && now - polledAt >= 50) {
        polledAt = now;
        if (!pollTelemetry()) cutPower("invalid servo mode or safety feedback");
    }
    // Bounded serial work keeps floods from starving expiry checks.
    for (unsigned i = 0; i < 128 && Serial.available(); ++i) {
        char c = char(Serial.read());
        if (c == '\n') {
            if (!discardingLine && lineLength) { line[lineLength] = 0; command(line); }
            lineLength = 0;
            discardingLine = false;
        } else if (c != '\r') {
            if (lineLength >= sizeof(line) - 1) { cutPower("serial line too long"); discardingLine = true; }
            else if (!discardingLine) line[lineLength++] = c;
        }
    }
    delay(1);
}
