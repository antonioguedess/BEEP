#include <stdio.h>
#include "driver/pulse_cnt.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// --- Hardware ---
#define ENC1_A 18, ENC1_B 19, ENC1_Z 21
#define ENC2_A 22, ENC2_B 23, ENC2_Z 25
#define PPR 3840

// --- Frequências ---
#define FREQUENCIA_MEDICAO_HZ 1000  // Precisão de 1ms para velocidade
#define FREQUENCIA_LOG_HZ     10    // 10 linhas por segundo no PC (ideal para 1h)

#define PERIOD_US_MEDICAO     (1000000 / FREQUENCIA_MEDICAO_HZ)
#define PERIOD_MS_LOG         (1000 / FREQUENCIA_LOG_HZ)

// --- Globais ---
volatile int pos1 = 0, pos2 = 0;
volatile float vel1 = 0, vel2 = 0;
int lastPos1 = 0, lastPos2 = 0, lastZ1 = 0, lastZ2 = 0;
pcnt_unit_handle_t pcntHandle1, pcntHandle2, pcntHandleZ1, pcntHandleZ2;

const float delta_t_min = (1.0f / FREQUENCIA_MEDICAO_HZ) / 60.0f;

// --- Callback de Alta Precisão (1000Hz) ---
static void periodic_timer_callback(void* arg) {
    int z1 = 0, z2 = 0;
    pcnt_unit_get_count(pcntHandle1, (int*)&pos1);
    pcnt_unit_get_count(pcntHandle2, (int*)&pos2);
    pcnt_unit_get_count(pcntHandleZ1, &z1);
    pcnt_unit_get_count(pcntHandleZ2, &z2);

    // Reset Z
    if (z1 != lastZ1) { pcnt_unit_clear_count(pcntHandle1); pos1 = 0; lastPos1 = 0; lastZ1 = z1; }
    if (z2 != lastZ2) { pcnt_unit_clear_count(pcntHandle2); pos2 = 0; lastPos2 = 0; lastZ2 = z2; }

    // RPM
    vel1 = (float)(pos1 - lastPos1) / (PPR * 4.0f) / delta_t_min;
    vel2 = (float)(pos2 - lastPos2) / (PPR * 4.0f) / delta_t_min;

    lastPos1 = pos1; lastPos2 = pos2;
}

// --- Setup PCNT (Mesma lógica anterior) ---
pcnt_unit_handle_t setupPCNT(gpio_num_t pinA, gpio_num_t pinB, bool quadrature) {
    pcnt_unit_config_t unit_config = { .high_limit = 32767, .low_limit = -32768, .flags.accum_count = true };
    pcnt_unit_handle_t unit = NULL;
    ESP_ERROR_CHECK(pcnt_new_unit(&unit_config, &unit));
    
    pcnt_glitch_filter_config_t filter_config = {.max_glitch_ns = 1000};
    ESP_ERROR_CHECK(pcnt_unit_set_glitch_filter(unit, &filter_config));

    pcnt_chan_config_t chan_config = {.edge_gpio_num = pinA, .level_gpio_num = quadrature ? pinB : -1};
    pcnt_channel_handle_t chan = NULL;
    ESP_ERROR_CHECK(pcnt_new_channel(unit, &chan_config, &chan));

    if (quadrature) {
        pcnt_channel_set_edge_action(chan, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
        pcnt_channel_set_level_action(chan, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
        // Canal B para 4x
        pcnt_chan_config_t chanB_cfg = {.edge_gpio_num = pinB, .level_gpio_num = pinA};
        pcnt_channel_handle_t chanB = NULL;
        pcnt_new_channel(unit, &chanB_cfg, &chanB);
        pcnt_channel_set_edge_action(chanB, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
        pcnt_channel_set_level_action(chanB, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    } else {
        pcnt_channel_set_edge_action(chan, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_HOLD);
    }
    pcnt_unit_enable(unit); pcnt_unit_clear_count(unit); pcnt_unit_start(unit);
    return unit;
}

void app_main(void) {
    // Desativa mensagens do sistema para o CSV não ficar sujo
    esp_log_level_set("*", ESP_LOG_NONE); 

    pcntHandle1 = setupPCNT(18, 19, true); pcntHandle2 = setupPCNT(22, 23, true);
    pcntHandleZ1 = setupPCNT(21, -1, false); pcntHandleZ2 = setupPCNT(25, -1, false);

    const esp_timer_create_args_t timer_args = { .callback = &periodic_timer_callback, .name = "m" };
    esp_timer_handle_t timer;
    esp_timer_create(&timer_args, &timer);
    esp_timer_start_periodic(timer, PERIOD_US_MEDICAO);

    // Cabeçalho para o CSV
    printf("timestamp_ms,pos1,pos2,vel1_rpm,vel2_rpm,erro_graus\n");

    while (1) {
        int p1 = pos1, p2 = pos2;
        float v1 = vel1, v2 = vel2;
        float erro_g = (float)(p2 - p1) * 360.0f / (PPR * 4.0f);
        
        // Print simples separado por vírgulas
        printf("%llu,%d,%d,%.2f,%.2f,%.2f\n", 
                esp_timer_get_time() / 1000, p1, p2, v1, v2, erro_g);

        vTaskDelay(pdMS_TO_TICKS(PERIOD_MS_LOG));
    }
}