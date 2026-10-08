#pragma once
#include <recomp/rt.h>

struct TestCase {
    recomp::u32 word;
    unsigned size;
    unsigned thumb;
    void (*fn)(recomp::Cpu*);
    const char* text;
};

extern const TestCase tests[];
extern const unsigned num_tests;
