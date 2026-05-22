// SPDX-License-Identifier: GPL-2.0
// Optional Linux BPF LSM enforcement program for MemGuardOS.
//
// This program is intentionally small and conservative: it denies open(2) on
// high-risk paths while the userspace loader is alive. It is not part of the
// default Python test path because loading BPF LSM programs requires root,
// BTF, CONFIG_BPF_LSM and a kernel whose active LSM list contains "bpf".

#include <linux/bpf.h>
#include <linux/errno.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "GPL";

struct vfsmount;
struct dentry;

struct path {
    struct vfsmount *mnt;
    struct dentry *dentry;
} __attribute__((preserve_access_index));

struct file {
    struct path f_path;
} __attribute__((preserve_access_index));

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
} denied_count SEC(".maps");

static __always_inline int streq(const char *path, const char *needle)
{
#pragma unroll
    for (int i = 0; i < 128; i++) {
        if (needle[i] == '\0')
            return path[i] == '\0';
        if (path[i] != needle[i])
            return 0;
    }
    return 0;
}

static __always_inline int contains(const char *path, const char *needle)
{
#pragma unroll
    for (int i = 0; i < 192; i++) {
        if (path[i] == '\0')
            return 0;
        int matched = 1;
#pragma unroll
        for (int j = 0; j < 32; j++) {
            if (needle[j] == '\0')
                break;
            if (path[i + j] != needle[j]) {
                matched = 0;
                break;
            }
        }
        if (matched)
            return 1;
    }
    return 0;
}

static __always_inline int sensitive_path(const char *path)
{
    if (streq(path, "/tmp/memguardos_lsm_secret") || contains(path, "/tmp/memguardos_lsm_secret"))
        return 1;
#ifdef MEMGUARDOS_PRODUCTION_DENYLIST
    if (streq(path, "/etc/shadow") || streq(path, "/etc/gshadow"))
        return 1;
    if (contains(path, "/.ssh/id_rsa") || contains(path, "/.ssh/id_ed25519"))
        return 1;
    if (contains(path, "/.aws/credentials") || contains(path, "/.kube/config"))
        return 1;
    if (contains(path, "/secrets/") || contains(path, "/service_account"))
        return 1;
    if (contains(path, "/.env"))
        return 1;
#endif
    return 0;
}

SEC("lsm/file_open")
int BPF_PROG(memguard_file_open, struct file *file, int ret)
{
    if (ret != 0)
        return ret;

    char path[256];
    long len = bpf_d_path(&file->f_path, path, sizeof(path));
    if (len < 0)
        return 0;

    if (!sensitive_path(path))
        return 0;

    __u32 key = 0;
    __u64 *count = bpf_map_lookup_elem(&denied_count, &key);
    if (count)
        __sync_fetch_and_add(count, 1);
    bpf_printk("memguardos denied open: %s\n", path);
    return -EACCES;
}
