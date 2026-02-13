// --- Includes ---

#include <stdio.h> // printf
#include "driver/pulse_cnt.h" // PCNT
#include "driver/gpio.h" // GPIO
#include "esp_timer.h" // Timers
#include "esp_err.h" // ESP Error Codes
#include "esp_log.h" // ESP Logging
#include "esp_clk_tree.h" // ESP Clock Functions
#include "freertos/FreeRTOS.h" // FreeRTOS
#include "freertos/task.h" // FreeRTOS Task
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include <string.h>
#include <sys/param.h>
#include "esp_system.h"
#include "esp_netif.h"
#include <stdint.h>

// --- Hardware ---
#define ENC1_A 18
#define ENC1_B 19
#define ENC1_Z 21
#define ENC2_A 22
#define ENC2_B 23
#define ENC2_Z 25
#define PPR 3840 // Pulses Per Revolution (with 4x decoding)

// --- Network ---
#define EXAMPLE_ESP_WIFI_SSID      "BEEP_SENSOR_NODE"
#define EXAMPLE_MAX_STA_CONN       4
#define BROADCAST_IP               "192.168.4.255"
#define UDP_PORT                   12345

// --- Frequencies ---
#define FREQUENCIA_MEDICAO_HZ 1000  // Measuring frequency in Hz
#define FREQUENCIA_LOG_HZ     10    // Logging frequency in Hz

#define PERIOD_US_MEDICAO     (1000000 / FREQUENCIA_MEDICAO_HZ) // Measuring period in microseconds
#define PERIOD_MS_LOG         (1000 / FREQUENCIA_LOG_HZ) // Logging period in milliseconds

// --- Global Variables ---
volatile int pos1 = 0, pos2 = 0; // Initial definition of position variables
volatile float vel1 = 0, vel2 = 0; // Initial definition of velocity variables
int lastPos1 = 0, lastPos2 = 0, lastZ1 = 0, lastZ2 = 0; // Last position and Z auxiliary variables
pcnt_unit_handle_t pcntHandle1, pcntHandle2, pcntHandleZ1, pcntHandleZ2; // PCNT Handles
const float delta_t_min = (1.0f / FREQUENCIA_MEDICAO_HZ) / 60.0f; // Time delta in minutes
volatile int erro = 0; // Timing variables for performance measurement

static const char *TAG = "BEEP_WIFI";

// Função para gerir eventos de Wi-Fi
static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Tentando reconectar ao Wi-Fi...");
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Conectado! IP:" IPSTR, IP2STR(&event->ip_info.ip));
    }
}

void wifi_init_sta(void) {
    esp_netif_init();
    esp_event_loop_create_default(); // Garante que o loop de eventos existe
    esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);

    // ADICIONA ESTAS LINHAS PARA USAR O HANDLER:
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);

    wifi_config_t wifi_config = {
        .ap = {
            .ssid = EXAMPLE_ESP_WIFI_SSID,
            .ssid_len = strlen(EXAMPLE_ESP_WIFI_SSID),
            .channel = 1,
            .authmode = WIFI_AUTH_OPEN,
            .max_connection = EXAMPLE_MAX_STA_CONN,
        },
    };

    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &wifi_config);
    esp_wifi_start();
}

void send_udp_broadcast(const char *payload) {
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(BROADCAST_IP);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(UDP_PORT);

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) return;

    int bc = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &bc, sizeof(bc));

    // Envia o "chute" de dados
    sendto(sock, payload, strlen(payload), 0, (struct sockaddr *)&dest_addr, sizeof(dest_addr));
    
    close(sock);
}

// --- High Precision Callback ---
// This function is triggered by a hardware timer interrupt at a fixed frequency (e.g., 1kHz)
static void periodic_timer_callback(void* arg) {
    int z1 = 0, z2 = 0;
    int64_t t1 = 0, t2 = 0;
    int current_pos1 = 0, current_pos2 = 0;

    // 1. Fetch raw pulse counts directly from the hardware PCNT units
    // current_pos stores the quadrature count (position)
    // z stores the cumulative count of Index pulses (rotations)
    t1 = esp_timer_get_time();
    pcnt_unit_get_count(pcntHandle1, &current_pos1);
    pcnt_unit_get_count(pcntHandle2, &current_pos2);
    pcnt_unit_get_count(pcntHandleZ1, &z1);
    pcnt_unit_get_count(pcntHandleZ2, &z2);
    t2 = esp_timer_get_time();
    

    // 2. CALCULATE VELOCITY (RPM) FIRST
    // We calculate RPM using the raw count BEFORE any reset logic.
    // This ensures continuity and avoids velocity spikes/drops when the Index (Z) resets the counter.
    // Formula: Delta_Position / (Resolution * 4 for quadrature) / Delta_Time_in_minutes
    vel1 = (float)(current_pos1 - lastPos1) / (PPR * 4.0f) / delta_t_min;
    vel2 = (float)(current_pos2 - lastPos2) / (PPR * 4.0f) / delta_t_min;

    // 3. INDEX (Z) RESET LOGIC for Absolute Position
    // Motor 1: Check if a new Index pulse was detected since the last execution
    if (z1 != lastZ1) { 
        pcnt_unit_clear_count(pcntHandle1); // Hardware reset of the pulse counter
        current_pos1 = 0;                  // Reset local variable to align with hardware
        lastPos1 = 0;                      // Prepare delta calculation for the next cycle
        lastZ1 = z1;                       // Update last known Index state
    } else {
        lastPos1 = current_pos1;           // Store current position for next RPM delta
    }

    // Motor 2: Same logic as Motor 1
    if (z2 != lastZ2) { 
        pcnt_unit_clear_count(pcntHandle2); 
        current_pos2 = 0; 
        lastPos2 = 0; 
        lastZ2 = z2; 
    } else {
        lastPos2 = current_pos2;
    }

    // 4. Update Global Variables
    // These volatile variables will be read by the main loop for logging/display
    pos1 = current_pos1;
    pos2 = current_pos2;
    erro = (int)(t2 - t1); // Store timing error for performance measurement
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
    
    // 1. Inicia NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    
    // 2. Inicia Wi-Fi (Mantém os logs ativos aqui para veres se liga!)
    wifi_init_sta();

    // 3. Hardware Initialization
    // Initialize PCNT units for position (quadrature) and rotation reference (Z-index)
    pcntHandle1  = setupPCNT(ENC1_A, ENC1_B, true);  // Motor 1 Position
    pcntHandle2  = setupPCNT(ENC2_A, ENC2_B, true);  // Motor 2 Position
    pcntHandleZ1 = setupPCNT(ENC1_Z, -1, false);     // Motor 1 Index/Reset
    pcntHandleZ2 = setupPCNT(ENC2_Z, -1, false);     // Motor 2 Index/Reset

    // 4. High-Precision Background Timer
    // Create and start a hardware timer to execute the measurement callback at a fixed frequency
    const esp_timer_create_args_t timer_args = { .callback = &periodic_timer_callback, .name = "m" };
    esp_timer_handle_t timer;
    esp_timer_create(&timer_args, &timer);
    esp_timer_start_periodic(timer, PERIOD_US_MEDICAO);

    // 5. CSV Header Output
    // Prints the column labels for easy parsing by Python/Excel
    printf("timestamp_ms,pos1,pos2,vel1_rpm,vel2_rpm,erro_graus,erro_ticks\n");

    // Opcional: silenciar logs aqui se quiseres a consola limpa para o CSV
    esp_log_level_set("*", ESP_LOG_NONE);

    // 6. Main Logging Loop
    // Periodically captures snapshots of the global data and exports them via Serial
    while (1) {
        // Capture a local "snapshot" of volatile variables to ensure data atomicity
        int p1 = pos1, p2 = pos2;
        float v1 = vel1, v2 = vel2;
        int erro1 = erro;

        // Calculate the angular error in degrees between the two shafts
        // Formula: (Delta_Pulses) * 360 / (Resolution * 4)
        float erro_g = (float)(p2 - p1) * 360.0f / (PPR * 4.0f);
        
        // Export data in CSV format: Time, Positions, Velocities, and Synchronism Error
        char data[128];
        sprintf(data, "%llu,%d,%d,%.2f,%.2f,%.2f,%d\n", 
                esp_timer_get_time(), p1, p2, v1, v2, erro_g, erro1);
        
        // Envia por USB (Standard Output)
        printf("%s", data); 
        
        // Envia por Wi-Fi
        send_udp_broadcast(data);

        // Block the task for a predefined logging period to free CPU resources
        vTaskDelay(pdMS_TO_TICKS(PERIOD_MS_LOG));
    }
}