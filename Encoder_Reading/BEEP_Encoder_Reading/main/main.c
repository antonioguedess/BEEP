// --- Includes ---
#include <stdio.h>
#include <string.h>
#include "driver/pulse_cnt.h"
#include "driver/gpio.h"
#include "hal/pcnt_ll.h"
#include "esp_timer.h"
#include "esp_err.h"
#include "esp_pm.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/queue.h"
#include "nvs_flash.h"

// --- Hardware ---
#define ENC1_A 18
#define ENC1_B 19
#define ENC1_Z 21
#define ENC2_A 22
#define ENC2_B 23
#define ENC2_Z 25
#define PPR 3840

#define BATCH_SIZE 100
#define SINGLE_ENTRY_SIZE 80 // Tamanho estimado de cada linha
char buffer_A[BATCH_SIZE * SINGLE_ENTRY_SIZE];
char buffer_B[BATCH_SIZE * SINGLE_ENTRY_SIZE];

char* volatile write_ptr = buffer_A;
char* volatile read_ptr = NULL;
int current_buffer_len = 0;

// --- Frequencies ---
#define FREQUENCY_HZ 1000 
#define PERIOD_US (1000000 / FREQUENCY_HZ)

// --- Global Variables ---
int lastPos1 = 0, lastPos2 = 0, lastZ1 = 0, lastZ2 = 0; 
pcnt_unit_handle_t pcntHandle1, pcntHandle2, pcntHandleZ1, pcntHandleZ2; 
const float delta_t_min = (1.0f / FREQUENCY_HZ) / 60.0f; 

// Estrutura para passar dados da ISR para a Main
typedef struct {
    int64_t t;
    int p1, p2;
    float v1, v2, erro;
    int ticks;
} data_sample_t;

QueueHandle_t data_queue;

// --- High Precision Callback ---
// This function is triggered by a hardware timer interrupt at a fixed frequency (e.g., 1kHz)
static void IRAM_ATTR periodic_timer_callback(void* arg) {
    int64_t t1 = esp_timer_get_time();
    int16_t c1 = pcnt_ll_get_count(&PCNT, 0); 
    int16_t c2 = pcnt_ll_get_count(&PCNT, 1);
    int16_t z1 = pcnt_ll_get_count(&PCNT, 2);
    int16_t z2 = pcnt_ll_get_count(&PCNT, 3);
    int64_t t2 = esp_timer_get_time();

    // Cálculos instantâneos
    float v1 = (float)(c1 - lastPos1) / (PPR * 4.0f) / delta_t_min;
    float v2 = (float)(c2 - lastPos2) / (PPR * 4.0f) / delta_t_min;

    // Reset por Index Z
    if (z1 != lastZ1) { pcnt_unit_clear_count(pcntHandle1); c1 = 0; lastPos1 = 0; lastZ1 = z1; } 
    else { lastPos1 = c1; }
    if (z2 != lastZ2) { pcnt_unit_clear_count(pcntHandle2); c2 = 0; lastZ2 = z2; } 
    else { lastPos2 = c2; }
    float erro = c2 - c1;

    data_sample_t sample = {
        .t = t1,
        .p1 = c1,
        .p2 = c2,
        .v1 = v1,
        .v2 = v2,
        .erro = erro,
        .ticks = (int)(t2 - t1) // Now measuring only the high-speed part!
    };

    // 2. Send the structure to the queue
    xQueueSendFromISR(data_queue, &sample, NULL);
}

// --- Setup Pulse Counter (PCNT) Unit ---
// This function initializes a hardware pulse counter unit for either Quadrature Encoders (4x) or simple Pulse Counting (Index/Z)
pcnt_unit_handle_t setupPCNT(gpio_num_t pinA, gpio_num_t pinB, bool quadrature) {
    
    // 1. Unit Configuration
    // Defines the 16-bit hardware counter limits and enables overflow accumulation (accum_count)
    pcnt_unit_config_t unit_config = { 
        .high_limit = 32767, 
        .low_limit = -32768, 
        .flags.accum_count = true // Allows the driver to track counts beyond 16-bit limits
    };
    pcnt_unit_handle_t unit = NULL;
    ESP_ERROR_CHECK(pcnt_new_unit(&unit_config, &unit));
    
    // 2. Glitch Filter Configuration (Signal Debouncing)
    // Ignores pulses shorter than 1000ns to eliminate high-frequency electromagnetic noise from motors
    pcnt_glitch_filter_config_t filter_config = {
        .max_glitch_ns = 1000 
    };
    ESP_ERROR_CHECK(pcnt_unit_set_glitch_filter(unit, &filter_config));
    //ESP_ERROR_CHECK(pcnt_unit_enable_glitch_filter(unit)); // Hardware-level noise suppression enabled

    // 3. Channel Configuration
    // Assigns physical GPIOs to the PCNT unit
    pcnt_chan_config_t chan_config = {
        .edge_gpio_num = pinA, 
        .level_gpio_num = quadrature ? pinB : -1 // If quadrature, B level informs direction
    };
    pcnt_channel_handle_t chan = NULL;
    ESP_ERROR_CHECK(pcnt_new_channel(unit, &chan_config, &chan));

    if (quadrature) {
        // --- 4x Decoding Logic (Quadrature Mode) ---
        // Setup Channel A: Actions for rising/falling edges based on Channel B level
        pcnt_channel_set_edge_action(chan, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
        pcnt_channel_set_level_action(chan, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
        
        // Setup Channel B: Required for full 4x resolution (decodes all transitions)
        pcnt_chan_config_t chanB_cfg = {
            .edge_gpio_num = pinB, 
            .level_gpio_num = pinA
        };
        pcnt_channel_handle_t chanB = NULL;
        ESP_ERROR_CHECK(pcnt_new_channel(unit, &chanB_cfg, &chanB));
        
        // Setup Channel B Actions: Inverse logic compared to Channel A to correctly increment/decrement
        pcnt_channel_set_edge_action(chanB, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
        pcnt_channel_set_level_action(chanB, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    } else {
        // --- Single Pulse Mode (Index/Z Pin) ---
        // Simply increments the counter on every rising edge of the Z signal
        pcnt_channel_set_edge_action(chan, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_HOLD);
    }

    // 4. Hardware Initialization and Startup
    ESP_ERROR_CHECK(pcnt_unit_enable(unit));  // Enable the PCNT unit power/clock
    ESP_ERROR_CHECK(pcnt_unit_clear_count(unit)); // Reset counter to zero
    ESP_ERROR_CHECK(pcnt_unit_start(unit));   // Begin autonomous hardware counting
    
    return unit; // Return the handle for future hardware interaction
}

void app_main(void) {
    // Forçar o CPU a 240MHz constantes e desativar power management
    esp_pm_config_t pm_config = {
        .max_freq_mhz = 240,
        .min_freq_mhz = 240,
        .light_sleep_enable = false
    };
    esp_pm_configure(&pm_config);

    nvs_flash_init();

    // Initialize the queue for 150 samples
    data_queue = xQueueCreate(150, sizeof(data_sample_t));

    if (data_queue == NULL) {
        printf("Failed to create queue!\n");
        return;
    }

    pcntHandle1  = setupPCNT(ENC1_A, ENC1_B, true);
    pcntHandle2  = setupPCNT(ENC2_A, ENC2_B, true);
    pcntHandleZ1 = setupPCNT(ENC1_Z, -1, false);
    pcntHandleZ2 = setupPCNT(ENC2_Z, -1, false);

    const esp_timer_create_args_t timer_args = { .callback = &periodic_timer_callback, .name = "phys" };
    esp_timer_handle_t timer;
    esp_timer_create(&timer_args, &timer);
    esp_timer_start_periodic(timer, PERIOD_US);

    printf("timestamp_us,pos1,pos2,vel1,vel2,erro_g,ticks\n");

    data_sample_t received_sample;
    int count = 0; // Declare count HERE, before the while loop

    while (1) {
        if (xQueueReceive(data_queue, &received_sample, portMAX_DELAY)) {
            
            // Usamos buffer_A diretamente para simplificar
            int space_left = (BATCH_SIZE * SINGLE_ENTRY_SIZE) - current_buffer_len;
            
            int written = snprintf(buffer_A + current_buffer_len, space_left,
                                   "$%lld,%d,%d,%.2f,%.2f,%.4f,%d\n",
                                   received_sample.t, received_sample.p1, 
                                   received_sample.p2, received_sample.v1, 
                                   received_sample.v2, received_sample.erro, 
                                   received_sample.ticks);

            if (written > 0 && written < space_left) {
                current_buffer_len += written;
            }

            if (++count >= BATCH_SIZE) {
                printf("%s", buffer_A); // Imprime o bloco
                current_buffer_len = 0;  // Reseta o tamanho
                buffer_A[0] = '\0';     // Limpa a string
                count = 0;              // Reseta o contador de amostras
            }
        }
    }
}