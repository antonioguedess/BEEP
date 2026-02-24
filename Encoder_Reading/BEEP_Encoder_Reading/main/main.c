#include <stdio.h>
#include <string.h>
#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_err.h"
#include "esp_pm.h"
#include "esp_cpu.h"
#include "esp_attr.h"
#include "nvs_flash.h"

#include "driver/pulse_cnt.h"
#include "driver/gptimer.h"
#include "driver/gpio.h"

#include "hal/pcnt_ll.h"
#include "xtensa/core-macros.h"

// ---------------- Hardware ----------------
#define ENC1_A 18
#define ENC1_B 19
#define ENC1_Z 21
#define ENC2_A 22
#define ENC2_B 23
#define ENC2_Z 25

#define PPR 3840

#define CPU_MHZ 240
// ---------------- Frequência de amostragem ----------------
#define FREQUENCY_HZ 2400
#define PERIOD_US (1000000 / FREQUENCY_HZ)

// ---------------- Print batching ----------------
#define BATCH_SIZE 100
#define SINGLE_ENTRY_SIZE 96
static char buffer_A[BATCH_SIZE * SINGLE_ENTRY_SIZE];
static int current_buffer_len = 0;

// ---------------- PCNT handles ----------------
static pcnt_unit_handle_t pcntHandle1, pcntHandle2, pcntHandleZ1, pcntHandleZ2;

// Estado (processado na task)
static int lastPos1 = 0, lastPos2 = 0, lastZ1 = 0, lastZ2 = 0;
static const float delta_t_min = (1.0f / FREQUENCY_HZ) / 60.0f; // (s) / 60 -> usado no teu cálculo

// ---------------- CCOUNT (ciclos CPU) ----------------
static volatile uint32_t ccount_overhead = 0;

static inline uint32_t IRAM_ATTR ccount_barrier(void) {
    uint32_t c;
    asm volatile ("rsr.ccount %0" : "=a"(c));
    asm volatile ("" ::: "memory");
    return c;
}

static void IRAM_ATTR calibrate_ccount_overhead(void) {
    uint32_t t1 = ccount_barrier();
    uint32_t t2 = ccount_barrier();
    ccount_overhead = (t2 - t1);
}

// ---------------- Struct cru vindo da ISR (só inteiros!) ----------------
typedef struct {
    uint64_t t_us;          // timestamp REAL do GPTimer (1 tick = 1 us)
    uint32_t t_cycles;      // CCOUNT (para medir janelas curtas)
    int16_t  c1, c2;
    int16_t  z1, z2;
    uint32_t ticks_cycles;  // (t2 - t1) - overhead
} isr_sample_t;

static QueueHandle_t isr_queue;

// ---------------- GPTimer ----------------
static gptimer_handle_t gptimer = NULL;

// ---------------- PCNT setup ----------------
static pcnt_unit_handle_t setupPCNT(gpio_num_t pinA, gpio_num_t pinB, bool quadrature) {
    pcnt_unit_config_t unit_config = {
        .high_limit = 32767,
        .low_limit  = -32768,
        .flags.accum_count = true
    };

    pcnt_unit_handle_t unit = NULL;
    ESP_ERROR_CHECK(pcnt_new_unit(&unit_config, &unit));

    pcnt_glitch_filter_config_t filter_config = {
        .max_glitch_ns = 1000
    };
    ESP_ERROR_CHECK(pcnt_unit_set_glitch_filter(unit, &filter_config));
    // Se quiseres mesmo ativar: (recomendo)

    pcnt_chan_config_t chan_config = {
        .edge_gpio_num  = pinA,
        .level_gpio_num = quadrature ? pinB : -1
    };

    pcnt_channel_handle_t chanA = NULL;
    ESP_ERROR_CHECK(pcnt_new_channel(unit, &chan_config, &chanA));

    if (quadrature) {
        // Canal A
        ESP_ERROR_CHECK(pcnt_channel_set_edge_action(chanA,
            PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE));
        ESP_ERROR_CHECK(pcnt_channel_set_level_action(chanA,
            PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE));

        // Canal B
        pcnt_chan_config_t chanB_cfg = {
            .edge_gpio_num  = pinB,
            .level_gpio_num = pinA
        };
        pcnt_channel_handle_t chanB = NULL;
        ESP_ERROR_CHECK(pcnt_new_channel(unit, &chanB_cfg, &chanB));

        ESP_ERROR_CHECK(pcnt_channel_set_edge_action(chanB,
            PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE));
        ESP_ERROR_CHECK(pcnt_channel_set_level_action(chanB,
            PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE));
    } else {
        // Z: conta só rising edge
        ESP_ERROR_CHECK(pcnt_channel_set_edge_action(chanA,
            PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_HOLD));
    }

    ESP_ERROR_CHECK(pcnt_unit_enable(unit));
    ESP_ERROR_CHECK(pcnt_unit_clear_count(unit));
    ESP_ERROR_CHECK(pcnt_unit_start(unit));

    return unit;
}

// ---------------- GPTimer callback (ISR) — SEM floats ----------------
static bool IRAM_ATTR gptimer_callback(gptimer_handle_t timer,
                                       const gptimer_alarm_event_data_t *edata,
                                       void *user_data)
{
    (void)edata; (void)user_data;

    // 1) timestamp REAL do GPTimer (us)
    uint64_t now_us = 0;
    gptimer_get_raw_count(timer, &now_us);

    // 2) mede janela curta em ciclos (CCOUNT)
    asm volatile ("" ::: "memory");
    uint32_t t1 = ccount_barrier();

    int16_t c1 = pcnt_ll_get_count(&PCNT, 0);
    int16_t c2 = pcnt_ll_get_count(&PCNT, 1);
    int16_t z1 = pcnt_ll_get_count(&PCNT, 2);
    int16_t z2 = pcnt_ll_get_count(&PCNT, 3);

    uint32_t t2 = ccount_barrier();
    asm volatile ("" ::: "memory");

    uint32_t raw = (t2 - t1);
    uint32_t ticks = (raw > ccount_overhead) ? (raw - ccount_overhead) : 0;

    // 3) Reagendar próximo alarme (timer free-running)
    static uint64_t next_alarm = 0;
    if (next_alarm == 0) {
        next_alarm = now_us + PERIOD_US;   // inicializa na 1ª chamada
    } else {
        next_alarm += PERIOD_US;           // mantém a fase ideal
    }

    gptimer_alarm_config_t next = {
        .alarm_count = next_alarm,
    };
    gptimer_set_alarm_action(timer, &next);

    // 4) Enviar para a queue
    isr_sample_t s = {
        .t_us = now_us,
        .t_cycles = t1,
        .c1 = c1, .c2 = c2, .z1 = z1, .z2 = z2,
        .ticks_cycles = ticks,
    };

    BaseType_t hp_task_woken = pdFALSE;
    xQueueSendFromISR(isr_queue, &s, &hp_task_woken);
    return (hp_task_woken == pdTRUE);
}

// ---------------- app_main ----------------
void app_main(void)
{
    // Fixar frequências (só tem efeito se PM estiver ativo no sdkconfig)
    esp_pm_config_t pm_config = {
        .max_freq_mhz = CPU_MHZ,
        .min_freq_mhz = CPU_MHZ,
        .light_sleep_enable = false
    };
    esp_pm_configure(&pm_config);

    ESP_ERROR_CHECK(nvs_flash_init());

    // Queue de amostras cruas da ISR
    isr_queue = xQueueCreate(300, sizeof(isr_sample_t)); // ~125 ms de folga a 2400 Hz
    if (!isr_queue) {
        printf("Failed to create isr_queue!\n");
        return;
    }

    // PCNT: 2 quadraturas + 2 Z
    pcntHandle1  = setupPCNT(ENC1_A, ENC1_B, true);
    pcntHandle2  = setupPCNT(ENC2_A, ENC2_B, true);
    pcntHandleZ1 = setupPCNT(ENC1_Z, -1, false);
    pcntHandleZ2 = setupPCNT(ENC2_Z, -1, false);

    calibrate_ccount_overhead();
    printf("ccount_overhead=%u cycles\n", (unsigned)ccount_overhead);

    // GPTimer: 1 MHz (1 tick = 1 us), alarme a PERIOD_US
    gptimer_config_t tconf = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1000000
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&tconf, &gptimer));

    gptimer_event_callbacks_t cbs = {
        .on_alarm = gptimer_callback,
    };
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(gptimer, &cbs, NULL));
    ESP_ERROR_CHECK(gptimer_enable(gptimer));

    gptimer_alarm_config_t alarm = {
        .alarm_count = PERIOD_US,              // primeiro disparo em PERIOD_US
        .flags.auto_reload_on_alarm = false,   // IMPORTANTÍSSIMO
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(gptimer, &alarm));
    ESP_ERROR_CHECK(gptimer_start(gptimer));

    // CSV header
    printf("timestamp_us,pos1,pos2,vel1_rpm,vel2_rpm,erro_g,ticks_cycles\n");

    int count = 0;
    buffer_A[0] = '\0';
    current_buffer_len = 0;

    while (1) {
        isr_sample_t s;
        if (xQueueReceive(isr_queue, &s, portMAX_DELAY)) {

            // timestamp em us (ciclos / MHz). Isto dá us truncado (ok para logging)
            uint32_t t_us = s.t_us;

            // Cálculos (floats) FORA da ISR
            float v1 = (float)(s.c1 - lastPos1) / (PPR * 4.0f) / delta_t_min;
            float v2 = (float)(s.c2 - lastPos2) / (PPR * 4.0f) / delta_t_min;
            float erro = 360.0f * (float)(s.c1 - s.c2) / (PPR * 4.0f);

            // Tratamento do Z e clear_count FORA da ISR
            if (s.z1 != lastZ1) {
                pcnt_unit_clear_count(pcntHandle1);
                lastPos1 = 0;
                lastZ1 = s.z1;
            } else {
                lastPos1 = s.c1;
            }

            if (s.z2 != lastZ2) {
                pcnt_unit_clear_count(pcntHandle2);
                lastPos2 = 0;
                lastZ2 = s.z2;
            } else {
                lastPos2 = s.c2;
            }

            int space_left = (BATCH_SIZE * SINGLE_ENTRY_SIZE) - current_buffer_len;
            int written = snprintf(buffer_A + current_buffer_len, space_left,
                       "$%" PRIu64 ",%d,%d,%.2f,%.2f,%.4f,%" PRIu32 "\n",
                       (uint64_t)t_us, (int)s.c1, (int)s.c2,
                       (double)v1, (double)v2, (double)erro,
                       (uint32_t)s.ticks_cycles);
            if (written > 0 && written < space_left) {
                current_buffer_len += written;
            }

            if (++count >= BATCH_SIZE) {
                printf("%s", buffer_A);
                count = 0;
                current_buffer_len = 0;
                buffer_A[0] = '\0';
            }
        }
    }
}