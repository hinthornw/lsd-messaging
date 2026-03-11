#ifndef LSMSG_H
#define LSMSG_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Memory management */
void lsmsg_free_string(char *s);

/* Slack */
int32_t lsmsg_slack_verify_signature(
    const char *signing_secret,
    const char *timestamp,
    const char *signature,
    const uint8_t *body,
    size_t body_len
);

/* Returns JSON string. Caller must free. */
char *lsmsg_slack_parse_webhook(
    const uint8_t *body,
    size_t body_len,
    const char *content_type
);

char *lsmsg_slack_strip_mentions(const char *text);

/* Teams */
char *lsmsg_teams_parse_webhook(const char *payload_json);
char *lsmsg_teams_strip_mentions(const char *text);

/* Utilities */
char *lsmsg_deterministic_thread_id(
    const char *platform,
    const char *workspace_id,
    const char *channel_id,
    const char *thread_id
);

/* Handler Registry */
int64_t lsmsg_registry_new(void);
void lsmsg_registry_free(int64_t handle);
int64_t lsmsg_registry_register(int64_t handle, const char *fields_json);
char *lsmsg_registry_match_event(int64_t handle, const char *event_json);
char *lsmsg_registry_process_slack_webhook(
    int64_t handle,
    const uint8_t *body,
    size_t body_len,
    const char *content_type,
    const char *signing_secret,
    const char *timestamp,
    const char *signature
);
char *lsmsg_registry_process_teams_webhook(
    int64_t handle,
    const uint8_t *body,
    size_t body_len
);

#ifdef __cplusplus
}
#endif

#endif /* LSMSG_H */
