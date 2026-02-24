idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES esp_driver_pcnt esp_driver_gpio esp_wifi esp_event nvs_flash lwip esp_timer esp_pm esp_driver_gptimer)