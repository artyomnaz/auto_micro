/*
 * Microscope controller firmware for Levenhuk MED 30T automation.
 *
 * Hardware:
 *   - Arduino UNO R3
 *   - 3x NEMA 17 bipolar steppers (1.8 deg, ~1.7 A)
 *   - 3x TMC2209 drivers (STEP/DIR + shared ENABLE + MS1/MS2/MS3)
 *   - Motor supply 12 V (LRS-75-12); logic via USB 5 V
 *   - TMC2209 Vref ≈ 0.60 V with Rsense = 0.11 Ohm
 *
 * Axes: F focus, M magnification, I illumination
 * Protocol: 115200 8N1, v1.1 — see host microscope_control.protocol
 *
 * Requires: AccelStepper library
 */

#include <AccelStepper.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

static const char *FIRMWARE_ID = "microscope_controller";
static const char *PROTOCOL_VERSION = "1.1";

// Shared enable (active LOW on typical TMC2209 / SilentStepStick boards)
static const uint8_t PIN_ENABLE = 8;

// Shared microstep select pins (1/16 => MS1=MS2=MS3=HIGH on many boards)
static const uint8_t PIN_MS1 = 9;
static const uint8_t PIN_MS2 = 10;
static const uint8_t PIN_MS3 = 11;
static const uint8_t MICROSTEP_MODE = 16;

// Focus
static const uint8_t PIN_F_STEP = 2;
static const uint8_t PIN_F_DIR = 5;
// Magnification
static const uint8_t PIN_M_STEP = 3;
static const uint8_t PIN_M_DIR = 6;
// Illumination
static const uint8_t PIN_I_STEP = 4;
static const uint8_t PIN_I_DIR = 7;

AccelStepper stepperF(AccelStepper::DRIVER, PIN_F_STEP, PIN_F_DIR);
AccelStepper stepperM(AccelStepper::DRIVER, PIN_M_STEP, PIN_M_DIR);
AccelStepper stepperI(AccelStepper::DRIVER, PIN_I_STEP, PIN_I_DIR);

struct AxisLimits {
  long minSteps;
  long maxSteps;
};

AxisLimits limitsF = {-200000L, 200000L};
AxisLimits limitsM = {-100000L, 100000L};
AxisLimits limitsI = {0L, 32000L};

bool driversEnabled = false;
char activeProfile[24] = "coarse";

static void applyMicrostepPins() {
  // 1/16 on common TMC2209 carrier boards
  pinMode(PIN_MS1, OUTPUT);
  pinMode(PIN_MS2, OUTPUT);
  pinMode(PIN_MS3, OUTPUT);
  digitalWrite(PIN_MS1, HIGH);
  digitalWrite(PIN_MS2, HIGH);
  digitalWrite(PIN_MS3, HIGH);
}

static void setDriversEnabled(bool enabled) {
  driversEnabled = enabled;
  digitalWrite(PIN_ENABLE, enabled ? LOW : HIGH);
  if (!enabled) {
    stepperF.moveTo(stepperF.currentPosition());
    stepperM.moveTo(stepperM.currentPosition());
    stepperI.moveTo(stepperI.currentPosition());
  }
}

static bool anyMoving() {
  return stepperF.isRunning() || stepperM.isRunning() || stepperI.isRunning();
}

static AccelStepper *pickStepper(char axis) {
  switch (axis) {
    case 'F': return &stepperF;
    case 'M': return &stepperM;
    case 'I': return &stepperI;
    default: return nullptr;
  }
}

static AxisLimits *pickLimits(char axis) {
  switch (axis) {
    case 'F': return &limitsF;
    case 'M': return &limitsM;
    case 'I': return &limitsI;
    default: return nullptr;
  }
}

static char normalizeAxis(const char *token) {
  if (token == nullptr || token[0] == '\0') return 0;
  char c = toupper(token[0]);
  if (c == 'Z') return 'F';
  if (c == 'F' || c == 'M' || c == 'I') return c;
  return 0;
}

static void replyOk() { Serial.println(F("ok")); }

static void replyError(const __FlashStringHelper *msg) {
  Serial.print(F("error:"));
  Serial.println(msg);
}

static void replyVersion() {
  Serial.print(F("version:id="));
  Serial.print(FIRMWARE_ID);
  Serial.print(F(",proto="));
  Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",axes=F/M/I,ms="));
  Serial.print(MICROSTEP_MODE);
  Serial.println();
}

static void replyLimits() {
  Serial.print(F("limits:"));
  Serial.print(F("fmin=")); Serial.print(limitsF.minSteps);
  Serial.print(F(",fmax=")); Serial.print(limitsF.maxSteps);
  Serial.print(F(",mmin=")); Serial.print(limitsM.minSteps);
  Serial.print(F(",mmax=")); Serial.print(limitsM.maxSteps);
  Serial.print(F(",imin=")); Serial.print(limitsI.minSteps);
  Serial.print(F(",imax=")); Serial.print(limitsI.maxSteps);
  Serial.println();
}

static void replyDiag() {
  Serial.print(F("diag:en="));
  Serial.print(driversEnabled ? 1 : 0);
  Serial.print(F(",mv="));
  Serial.print(anyMoving() ? 1 : 0);
  Serial.print(F(",ms="));
  Serial.print(MICROSTEP_MODE);
  Serial.print(F(",pf="));
  Serial.println(activeProfile);
}

static void replyStatus() {
  Serial.print(F("status:"));
  Serial.print(F("en=")); Serial.print(driversEnabled ? 1 : 0);
  Serial.print(F(",mv=")); Serial.print(anyMoving() ? 1 : 0);
  Serial.print(F(",f=")); Serial.print(stepperF.currentPosition());
  Serial.print(F(",m=")); Serial.print(stepperM.currentPosition());
  Serial.print(F(",i=")); Serial.print(stepperI.currentPosition());
  Serial.print(F(",tf=")); Serial.print(stepperF.targetPosition());
  Serial.print(F(",tm=")); Serial.print(stepperM.targetPosition());
  Serial.print(F(",ti=")); Serial.print(stepperI.targetPosition());
  Serial.print(F(",sf=")); Serial.print(stepperF.maxSpeed(), 3);
  Serial.print(F(",sm=")); Serial.print(stepperM.maxSpeed(), 3);
  Serial.print(F(",si=")); Serial.print(stepperI.maxSpeed(), 3);
  Serial.print(F(",af=")); Serial.print(stepperF.acceleration(), 3);
  Serial.print(F(",am=")); Serial.print(stepperM.acceleration(), 3);
  Serial.print(F(",ai=")); Serial.print(stepperI.acceleration(), 3);
  Serial.print(F(",ms=")); Serial.print(MICROSTEP_MODE);
  Serial.print(F(",pf=")); Serial.println(activeProfile);
}

static bool parseLong(const char *token, long *out) {
  if (token == nullptr || *token == '\0') return false;
  char *end = nullptr;
  long value = strtol(token, &end, 10);
  if (end == token || (end != nullptr && *end != '\0')) return false;
  *out = value;
  return true;
}

static bool parseFloat(const char *token, float *out) {
  if (token == nullptr || *token == '\0') return false;
  char *end = nullptr;
  float value = strtod(token, &end);
  if (end == token || (end != nullptr && *end != '\0')) return false;
  *out = value;
  return true;
}

static bool cmdGoto(char axis, long position, bool reply) {
  if (!driversEnabled) {
    if (reply) replyError(F("disabled"));
    return false;
  }
  AccelStepper *stepper = pickStepper(axis);
  AxisLimits *limits = pickLimits(axis);
  if (stepper == nullptr || limits == nullptr) {
    if (reply) replyError(F("unknown axis"));
    return false;
  }
  if (position < limits->minSteps || position > limits->maxSteps) {
    if (reply) replyError(F("soft limit"));
    return false;
  }
  stepper->moveTo(position);
  if (reply) replyOk();
  return true;
}

static void handleSync(char **saveptr) {
  if (!driversEnabled) {
    replyError(F("disabled"));
    return;
  }
  // Validate all tokens, then apply targets together.
  long fPos = stepperF.currentPosition();
  long mPos = stepperM.currentPosition();
  long iPos = stepperI.currentPosition();
  bool setF = false, setM = false, setI = false;

  char *token = strtok_r(nullptr, " \t", saveptr);
  if (token == nullptr) {
    replyError(F("empty sync"));
    return;
  }
  while (token != nullptr) {
    char *eq = strchr(token, '=');
    if (eq == nullptr || eq == token || *(eq + 1) == '\0') {
      replyError(F("bad sync token"));
      return;
    }
    *eq = '\0';
    char axis = normalizeAxis(token);
    long value = 0;
    if (axis == 0 || !parseLong(eq + 1, &value)) {
      replyError(F("bad sync token"));
      return;
    }
    AxisLimits *limits = pickLimits(axis);
    if (limits == nullptr || value < limits->minSteps || value > limits->maxSteps) {
      replyError(F("soft limit"));
      return;
    }
    if (axis == 'F') { fPos = value; setF = true; }
    if (axis == 'M') { mPos = value; setM = true; }
    if (axis == 'I') { iPos = value; setI = true; }
    token = strtok_r(nullptr, " \t", saveptr);
  }
  if (setF) stepperF.moveTo(fPos);
  if (setM) stepperM.moveTo(mPos);
  if (setI) stepperI.moveTo(iPos);
  replyOk();
}

static void handleLine(char *line) {
  size_t n = strlen(line);
  while (n > 0 && (line[n - 1] == '\r' || line[n - 1] == '\n' || line[n - 1] == ' ')) {
    line[--n] = '\0';
  }
  if (n == 0) {
    replyError(F("empty"));
    return;
  }

  char *save = nullptr;
  char *op = strtok_r(line, " \t", &save);
  if (op == nullptr) {
    replyError(F("empty"));
    return;
  }
  for (char *p = op; *p; ++p) *p = toupper(*p);

  if (strcmp(op, "PING") == 0) { replyOk(); return; }
  if (strcmp(op, "VERSION") == 0) { replyVersion(); return; }
  if (strcmp(op, "ENABLE") == 0) { setDriversEnabled(true); replyOk(); return; }
  if (strcmp(op, "DISABLE") == 0) { setDriversEnabled(false); replyOk(); return; }
  if (strcmp(op, "STOP") == 0) {
    stepperF.moveTo(stepperF.currentPosition());
    stepperM.moveTo(stepperM.currentPosition());
    stepperI.moveTo(stepperI.currentPosition());
    replyOk();
    return;
  }
  if (strcmp(op, "STATUS") == 0) { replyStatus(); return; }
  if (strcmp(op, "LIMITS") == 0) { replyLimits(); return; }
  if (strcmp(op, "DIAG") == 0) { replyDiag(); return; }
  if (strcmp(op, "SYNC") == 0) { handleSync(&save); return; }

  if (strcmp(op, "SETPROFILE") == 0) {
    char *name = strtok_r(nullptr, " \t", &save);
    if (name == nullptr) { replyError(F("missing profile")); return; }
    strncpy(activeProfile, name, sizeof(activeProfile) - 1);
    activeProfile[sizeof(activeProfile) - 1] = '\0';
    replyOk();
    return;
  }

  char *a1 = strtok_r(nullptr, " \t", &save);
  char *a2 = strtok_r(nullptr, " \t", &save);
  char axis = normalizeAxis(a1);

  if (strcmp(op, "MOVE") == 0) {
    long steps = 0;
    if (axis == 0 || !parseLong(a2, &steps)) { replyError(F("bad args")); return; }
    AccelStepper *stepper = pickStepper(axis);
    cmdGoto(axis, stepper->currentPosition() + steps, true);
    return;
  }
  if (strcmp(op, "GOTO") == 0) {
    long pos = 0;
    if (axis == 0 || !parseLong(a2, &pos)) { replyError(F("bad args")); return; }
    cmdGoto(axis, pos, true);
    return;
  }
  if (strcmp(op, "HOME") == 0) {
    if (axis == 0) { replyError(F("bad args")); return; }
    cmdGoto(axis, 0, true);
    return;
  }
  if (strcmp(op, "SETPOS") == 0) {
    long pos = 0;
    AccelStepper *stepper = pickStepper(axis);
    AxisLimits *limits = pickLimits(axis);
    if (stepper == nullptr || limits == nullptr || !parseLong(a2, &pos)) {
      replyError(F("bad args"));
      return;
    }
    if (pos < limits->minSteps || pos > limits->maxSteps) {
      replyError(F("soft limit"));
      return;
    }
    stepper->setCurrentPosition(pos);
    stepper->moveTo(pos);
    replyOk();
    return;
  }
  if (strcmp(op, "SETSPEED") == 0) {
    float spd = 0;
    AccelStepper *stepper = pickStepper(axis);
    if (stepper == nullptr || !parseFloat(a2, &spd) || spd <= 0.0f) {
      replyError(F("bad args"));
      return;
    }
    stepper->setMaxSpeed(spd);
    replyOk();
    return;
  }
  if (strcmp(op, "SETACC") == 0) {
    float acc = 0;
    AccelStepper *stepper = pickStepper(axis);
    if (stepper == nullptr || !parseFloat(a2, &acc) || acc <= 0.0f) {
      replyError(F("bad args"));
      return;
    }
    stepper->setAcceleration(acc);
    replyOk();
    return;
  }

  replyError(F("unknown command"));
}

static char lineBuf[128];
static uint8_t lineLen = 0;

void setup() {
  pinMode(PIN_ENABLE, OUTPUT);
  applyMicrostepPins();
  setDriversEnabled(false);

  stepperF.setMaxSpeed(800);
  stepperF.setAcceleration(1600);
  stepperM.setMaxSpeed(600);
  stepperM.setAcceleration(1200);
  stepperI.setMaxSpeed(400);
  stepperI.setAcceleration(800);

  Serial.begin(115200);
  while (!Serial) { ; }
  Serial.println(F("microscope_controller ready proto=1.1"));
}

void loop() {
  stepperF.run();
  stepperM.run();
  stepperI.run();

  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      handleLine(lineBuf);
      lineLen = 0;
    } else if (c != '\r') {
      if (lineLen < sizeof(lineBuf) - 1) {
        lineBuf[lineLen++] = c;
      } else {
        lineLen = 0;
        replyError(F("line too long"));
      }
    }
  }
}
