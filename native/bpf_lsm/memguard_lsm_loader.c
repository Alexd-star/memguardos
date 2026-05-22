// SPDX-License-Identifier: Apache-2.0
// Userspace loader for the optional MemGuardOS BPF LSM program.

#include <bpf/libbpf.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static volatile sig_atomic_t exiting;

static void on_signal(int signo)
{
    (void)signo;
    exiting = 1;
}

int main(int argc, char **argv)
{
    const char *object_path = argc > 1 ? argv[1] : "build/memguard_lsm.bpf.o";
    struct bpf_object *obj = NULL;
    struct bpf_program *prog = NULL;
    struct bpf_link *link = NULL;
    int err = 1;

    libbpf_set_strict_mode(LIBBPF_STRICT_ALL);

    obj = bpf_object__open_file(object_path, NULL);
    if (!obj) {
        fprintf(stderr, "failed to open BPF object: %s\n", object_path);
        goto cleanup;
    }
    if (bpf_object__load(obj)) {
        fprintf(stderr, "failed to load BPF object; check BPF LSM/BTF/root permissions\n");
        goto cleanup;
    }
    prog = bpf_object__find_program_by_name(obj, "memguard_file_open");
    if (!prog) {
        fprintf(stderr, "program memguard_file_open not found\n");
        goto cleanup;
    }
    link = bpf_program__attach_lsm(prog);
    if (!link) {
        fprintf(stderr, "failed to attach BPF LSM program\n");
        goto cleanup;
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    puts("MemGuardOS BPF LSM enforcer attached. Press Ctrl+C to detach.");
    fflush(stdout);
    while (!exiting)
        sleep(1);
    err = 0;

cleanup:
    bpf_link__destroy(link);
    bpf_object__close(obj);
    return err;
}
