#include "../native/ui/src/connection_liveness.h"
#include <cassert>
#include <string>
int main() {
    ConnectionLiveness h;
    assert(h.due(0)); assert(!h.fresh(0));
    h.begin("one", 100);
    assert(!h.accept("wrong", 200)); assert(!h.fresh(200));
    assert(h.accept("one", 1100)); assert(h.rttMs() == 1000);
    assert(h.fresh(1100)); assert(!h.accept("one", 1200)); // replay
    assert(std::string(h.state(27100)) == "delayed");
    assert(!h.fresh(46100)); assert(std::string(h.state(46100)) == "unresponsive");
    h.begin("two", 47000); assert(!h.accept("one", 48000));
    assert(!h.accept("two", 68001)); // delayed stale pong
    h.begin("three", 69000); assert(!h.accept("three", 68999));
    assert(h.accept("three", 70000));
    h.reset(); assert(!h.fresh(70000)); assert(!h.accept("three", 70001));
    h.begin("four", 71000); assert(!h.due(86000)); assert(h.due(91000));
}
