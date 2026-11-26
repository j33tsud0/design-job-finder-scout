#include <curl/curl.h>
#include <jansson.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_RESPONSE (2U * 1024U * 1024U)
#define MAX_QUERY 512U
#define MAX_RESULTS 10U

struct buffer {
    char *data;
    size_t length;
};

static size_t receive_data(
    char *ptr,
    size_t size,
    size_t count,
    void *userdata)
{
    struct buffer *buf = userdata;

    if (size != 0 && count > SIZE_MAX / size)
        return 0;

    const size_t bytes = size * count;

    if (bytes > MAX_RESPONSE - buf->length)
        return 0;

    char *next = realloc(
        buf->data,
        buf->length + bytes + 1U
    );

    if (next == NULL)
        return 0;

    buf->data = next;
    memcpy(buf->data + buf->length, ptr, bytes);
    buf->length += bytes;
    buf->data[buf->length] = '\0';

    return bytes;
}

static int safe_url(const char *url)
{
    return url != NULL &&
        (strncmp(url, "https://", 8) == 0 ||
         strncmp(url, "http://", 7) == 0);
}

static const char *string_field(
    const json_t *object,
    const char *key)
{
    const char *value =
        json_string_value(json_object_get(object, key));

    return value != NULL ? value : "";
}

static int emit_results(const struct buffer *buf)
{
    json_error_t error;

    json_t *root = json_loadb(
        buf->data,
        buf->length,
        JSON_REJECT_DUPLICATES,
        &error
    );

    if (root == NULL) {
        fprintf(stderr, "Invalid search response\n");
        return EXIT_FAILURE;
    }

    json_t *results = json_object_get(root, "jobs");
    if (!json_is_array(results)) {
        json_decref(root);
        fprintf(stderr, "Unexpected API response\\n");
        return EXIT_FAILURE;
    }
    json_t *output = json_array();

    if (output == NULL) {
        json_decref(root);
        return EXIT_FAILURE;
    }

    size_t i;
    json_t *item;

    json_array_foreach(results, i, item) {
        if (json_array_size(output) >= MAX_RESULTS)
            break;

        const char *url = string_field(item, "url");

        if (!safe_url(url))
            continue;

        json_t *entry = json_pack(
            "{s:s,s:s,s:s,s:i,s:s}",
            "title", string_field(item, "title"),
            "url", url,
            "description", string_field(item, "company_name"),
            "rank", (int)(i + 1U),
            "age", string_field(item, "publication_date")
        );

        if (entry == NULL ||
            json_array_append_new(output, entry) != 0) {
            json_decref(output);
            json_decref(root);
            return EXIT_FAILURE;
        }
    }

    const int status = json_dumpf(
        output,
        stdout,
        JSON_COMPACT
    );

    json_decref(output);
    json_decref(root);

    if (status != 0)
        return EXIT_FAILURE;

    return putchar('\n') == EOF
        ? EXIT_FAILURE : EXIT_SUCCESS;
}

int main(int argc, char **argv)
{
    if (argc != 2 ||
        strlen(argv[1]) == 0 ||
        strlen(argv[1]) > MAX_QUERY) {
        fprintf(stderr, "Usage: scout 'search query'\n");
        return EXIT_FAILURE;
    }

    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK)
        return EXIT_FAILURE;

    CURL *curl = curl_easy_init();
    struct curl_slist *headers = NULL;
    struct buffer response = {NULL, 0};
    char *encoded = NULL;

    int status = EXIT_FAILURE;
    long http_status = 0;
    char url[2048];

    if (curl == NULL)
        goto cleanup;

    encoded = curl_easy_escape(curl, argv[1], 0);

    if (encoded == NULL)
        goto cleanup;

    int n = snprintf(
        url,
        sizeof url,
        "https://remotive.com/api/remote-jobs?search=%s",
        encoded
    );

    if (n < 0 || (size_t)n >= sizeof url)
        goto cleanup;


    headers = curl_slist_append(
        NULL,
        "Accept: application/json"
    );

    if (headers == NULL)
        goto cleanup;

#define SETOPT(option, value) \
    do { \
        if (curl_easy_setopt(curl, option, value) != CURLE_OK) \
            goto cleanup; \
    } while (0)

    SETOPT(CURLOPT_URL, url);
    SETOPT(CURLOPT_HTTPHEADER, headers);
    SETOPT(CURLOPT_WRITEFUNCTION, receive_data);
    SETOPT(CURLOPT_WRITEDATA, &response);
    SETOPT(CURLOPT_CONNECTTIMEOUT, 8L);
    SETOPT(CURLOPT_TIMEOUT, 20L);
    SETOPT(CURLOPT_FOLLOWLOCATION, 0L);
    SETOPT(CURLOPT_SSL_VERIFYPEER, 1L);
    SETOPT(CURLOPT_SSL_VERIFYHOST, 2L);
    SETOPT(CURLOPT_NOSIGNAL, 1L);
    SETOPT(CURLOPT_ACCEPT_ENCODING, "");
    SETOPT(CURLOPT_USERAGENT, "OpportunityScout/0.2");

#undef SETOPT

    const CURLcode result = curl_easy_perform(curl);

    if (result != CURLE_OK) {
        fprintf(stderr, "Search request failed: %s\n",
                curl_easy_strerror(result));
        goto cleanup;
    }

    if (curl_easy_getinfo(
            curl,
            CURLINFO_RESPONSE_CODE,
            &http_status) != CURLE_OK)
        goto cleanup;

    if (http_status != 200) {
        fprintf(stderr, "Search API returned HTTP %ld\n",
                http_status);
        goto cleanup;
    }

    if (response.data != NULL)
        status = emit_results(&response);

cleanup:
    free(response.data);
    curl_free(encoded);

    if (curl != NULL)
        curl_easy_cleanup(curl);

    curl_slist_free_all(headers);
    curl_global_cleanup();

    return status;
}
