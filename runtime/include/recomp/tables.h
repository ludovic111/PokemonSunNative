// Tables emitted by the recompiler: where every translated function and block lives.
#pragma once

#include <recomp/rt.h>

namespace recomp {

struct Entry {
    u32 key; // address (module-relative for CRO modules) | Thumb bit
    HostFn fn;
};

struct ImageInfo {
    const char* name;      // "static" for the main executable, else the CRO module name
    bool module_relative;  // addresses are offsets from the module's load address
    u32 code_start, code_end;
    const Entry* entries;  // function entry points, sorted by key
    u32 num_entries;
    const Entry* labels;   // every block that can be resumed, with the function containing it
    u32 num_labels;
};

extern const ImageInfo images[];
extern const u32 num_images;

} // namespace recomp
