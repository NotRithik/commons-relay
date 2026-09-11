#pragma once
#include <cstdint>
#include <string>

// Use a monotonic clock supplied by the caller. Old inbox messages, unmatched
// challenges and delayed cached responses can never renew connection health.
class ConnectionLiveness {
public:
    static constexpr std::int64_t IntervalMs = 5000;
    static constexpr std::int64_t ReplyDeadlineMs = 8000;
    static constexpr std::int64_t StaleMs = 20000;
    void reset() { firstAttempt_ = -1; lastAttempt_ = -IntervalMs; lastReply_ = -1; sent_ = -1; rtt_ = -1; nonce_.clear(); }
    bool due(std::int64_t now) const { return now - lastAttempt_ >= IntervalMs && (nonce_.empty() || now - sent_ >= ReplyDeadlineMs); }
    void begin(const std::string& nonce, std::int64_t now) { if (firstAttempt_ < 0) firstAttempt_ = now; nonce_ = nonce; sent_ = now; lastAttempt_ = now; }
    bool accept(const std::string& nonce, std::int64_t now) {
        if (nonce_.empty() || nonce != nonce_ || now < sent_ || now - sent_ > ReplyDeadlineMs) return false;
        lastReply_ = now; rtt_ = now - sent_; nonce_.clear(); return true;
    }
    bool fresh(std::int64_t now) const { return lastReply_ >= 0 && now >= lastReply_ && now - lastReply_ < StaleMs; }
    std::int64_t ageMs(std::int64_t now) const { return lastReply_ < 0 ? -1 : now - lastReply_; }
    std::int64_t rttMs() const { return rtt_; }
    const char* state(std::int64_t now) const {
        if (lastReply_ < 0) return firstAttempt_ >= 0 && now - firstAttempt_ >= StaleMs ? "unresponsive" : "checking";
        if (!fresh(now)) return "unresponsive";
        return ageMs(now) >= 25000 ? "delayed" : "online";
    }
private:
    std::int64_t firstAttempt_ = -1, lastAttempt_ = -IntervalMs, lastReply_ = -1, sent_ = -1, rtt_ = -1;
    std::string nonce_;
};
