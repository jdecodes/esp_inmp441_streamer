#include <cmath>
#include <cstdint>
#include <cstring>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"

// ---- config ----
constexpr const char* WIFI_SSID = "your_network"; // add you wifiname
constexpr const char* WIFI_PASS = "your_password"; // add your password
constexpr const char* HOST_IP   = "192.168.1.4";  // laptop IP (ipconfig) add your laptop ip
constexpr uint16_t    HOST_PORT = 5000;
constexpr bool        USE_SINE  = false;           // true = send 440 Hz test tone
constexpr int         SHIFT     = 14;              // 8 clips, 16 is quiet

constexpr gpio_num_t PIN_BCLK = GPIO_NUM_4;
constexpr gpio_num_t PIN_WS   = GPIO_NUM_5;
constexpr gpio_num_t PIN_DIN  = GPIO_NUM_6;
constexpr uint32_t SAMPLE_RATE = 16000;
constexpr size_t N = 512;


// One I2S frame = one WS cycle = [left slot word][right slot word]
constexpr size_t SLOTS_PER_FRAME  = 2;
constexpr size_t LEFT_SLOT_INDEX  = 0;     // INMP441 with L/R pin tied to GND
constexpr size_t FRAMES_PER_PACKET = 512;  // audio samples per UDP packet
constexpr size_t WORDS_PER_READ   = FRAMES_PER_PACKET * SLOTS_PER_FRAME;
constexpr int    DISCARD_READS    = 16;    // ~0.5 s of startup junk

static const char* TAG = "mic";

constexpr int64_t MICROSECONDS_PER_SECOND = 1000000;

struct __attribute__((packed)) Pkt {
    uint32_t seq;                       // header
    int16_t  s[FRAMES_PER_PACKET];      // audio data
};
constexpr size_t HEADER_SIZE_BYTES = sizeof(uint32_t);



// ---- wifi + udp ----
static EventGroupHandle_t s_ev;
static int s_sock = -1;
static sockaddr_in s_dest;

static void on_event(void*, esp_event_base_t base, int32_t id, void*) {
    if (base == WIFI_EVENT && (id == WIFI_EVENT_STA_START ||
                               id == WIFI_EVENT_STA_DISCONNECTED)) {
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_ev, BIT0);
    }
}

static void net_init() {
    ESP_ERROR_CHECK(nvs_flash_init());
    s_ev = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t icfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&icfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, nullptr));

    wifi_config_t wc = {};
    strncpy(reinterpret_cast<char*>(wc.sta.ssid), WIFI_SSID, sizeof(wc.sta.ssid) - 1);
    strncpy(reinterpret_cast<char*>(wc.sta.password), WIFI_PASS, sizeof(wc.sta.password) - 1);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_ps(WIFI_PS_NONE);  // lower jitter
    xEventGroupWaitBits(s_ev, BIT0, pdFALSE, pdTRUE, portMAX_DELAY);

    s_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    s_dest.sin_family = AF_INET;
    s_dest.sin_port = htons(HOST_PORT);
    inet_pton(AF_INET, HOST_IP, &s_dest.sin_addr);
    ESP_LOGI(TAG, "wifi up, sending to %s:%u", HOST_IP, HOST_PORT);
}

// ---- main ----
static int32_t rx_buf[WORDS_PER_READ];
static Pkt pkt;

extern "C" void app_main(void)
{
    net_init();

    i2s_chan_handle_t rx = nullptr;
    i2s_chan_config_t chan_cfg =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, nullptr, &rx));

    i2s_std_config_t cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_BCLK,
            .ws = PIN_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = PIN_DIN,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx, &cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(rx));

    uint32_t seq = 0, reads = 0;
    float phase = 0.f;
    const float step = 2.f * 3.14159265f * 440.f / SAMPLE_RATE;

    int64_t rate_window_start_us = esp_timer_get_time();
    size_t frames_in_window = 0;

    while (true) {
        size_t br = 0;
        esp_err_t err = i2s_channel_read(rx, rx_buf, sizeof(rx_buf), &br, portMAX_DELAY);
        if (err != ESP_OK || br == 0) continue;

        size_t count = br / sizeof(int32_t);

        size_t words_read  = count;
        size_t frame_count = words_read / SLOTS_PER_FRAME;

        // rate check: frames/s should now be ~SAMPLE_RATE
        frames_in_window += frame_count;
        if (++reads < DISCARD_READS) continue;

        int64_t now_us = esp_timer_get_time();
        int64_t elapsed_us = now_us - rate_window_start_us;
        if (elapsed_us >= MICROSECONDS_PER_SECOND) {
            int64_t words_per_second = frames_in_window * MICROSECONDS_PER_SECOND / elapsed_us;
            // ESP_LOGI(TAG, "I2S words/s = %lld (expected %u)",
            //         static_cast<long long>(words_per_second),
            //         static_cast<unsigned>(SAMPLE_RATE));
            rate_window_start_us = now_us;
            frames_in_window = 0;
        }

        for (size_t frame = 0; frame < frame_count; ++frame) {
            int32_t left_word = rx_buf[frame * SLOTS_PER_FRAME + LEFT_SLOT_INDEX];
            int32_t value = left_word >> SHIFT;
            // clamp, USE_SINE override, rms/min/max stats: unchanged
            if (USE_SINE) {
                value = static_cast<int32_t>(8000.f * sinf(phase));
                phase += step;
                if (phase > 6.2831853f) phase -= 6.2831853f;
            }
            pkt.s[frame] = static_cast<int16_t>(value);
        }

        pkt.seq = seq++;
        size_t payload_bytes = frame_count * sizeof(int16_t);
        sendto(s_sock, &pkt, HEADER_SIZE_BYTES + payload_bytes, 0,
            reinterpret_cast<sockaddr*>(&s_dest), sizeof(s_dest));

    }
}