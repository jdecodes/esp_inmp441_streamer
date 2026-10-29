idf_component_register(SRCS "main.cpp"
                    INCLUDE_DIRS "."
                    REQUIRES esp_timer esp_driver_gpio esp_driver_i2s esp_wifi nvs_flash esp_event esp_netif)
