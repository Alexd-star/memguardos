// SPDX-License-Identifier: GPL-2.0
// Generated/customizable denylist hooks for MemGuardOS BPF LSM.
//
// The default policy is intentionally empty. Use
// scripts/generate_bpf_lsm_policy.py to create a reviewable static policy from
// JSON before building on an isolated test machine.

#ifndef MEMGUARD_LSM_POLICY_H
#define MEMGUARD_LSM_POLICY_H

static __always_inline int memguard_custom_sensitive_path(const char *path)
{
    (void)path;
    return 0;
}

#endif
