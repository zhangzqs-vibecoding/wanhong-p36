#include <unicorn/unicorn.h>

struct block_budget {
    size_t remaining_bytes;
    uc_err (*stop)(uc_engine *);
};

void p36_stop_at_block_budget(uc_engine *engine, uint64_t address, uint32_t size,
                             void *user_data)
{
    (void)address;
    struct block_budget *budget = user_data;
    if (budget->remaining_bytes < size) {
        budget->stop(engine);
        return;
    }
    budget->remaining_bytes -= size;
}
