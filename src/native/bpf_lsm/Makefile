BUILD_DIR ?= build
BPF_CLANG ?= clang
CC ?= cc
PKG_CONFIG ?= pkg-config
ARCH_UAPI_INCLUDE ?= /usr/include/$(shell uname -m)-linux-gnu
BPF_CFLAGS ?= -O2 -g -Wall -Werror -target bpf -I$(ARCH_UAPI_INCLUDE) $(BPF_EXTRA_CFLAGS)
USER_CFLAGS ?= -O2 -g -Wall -Wextra

.PHONY: all clean probe

all: $(BUILD_DIR)/memguard_lsm.bpf.o $(BUILD_DIR)/memguard_lsm_loader

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/memguard_lsm.bpf.o: memguard_lsm.bpf.c | $(BUILD_DIR)
	$(BPF_CLANG) $(BPF_CFLAGS) -c $< -o $@

$(BUILD_DIR)/memguard_lsm_loader: memguard_lsm_loader.c | $(BUILD_DIR)
	$(CC) $(USER_CFLAGS) $< -o $@ $$($(PKG_CONFIG) --cflags --libs libbpf)

probe:
	@test -r /sys/kernel/btf/vmlinux && echo "BTF: yes" || echo "BTF: no"
	@grep -qw bpf /sys/kernel/security/lsm 2>/dev/null && echo "BPF LSM: active" || echo "BPF LSM: inactive"
	@command -v $(BPF_CLANG) >/dev/null && echo "clang: yes" || echo "clang: no"
	@$(PKG_CONFIG) --exists libbpf && echo "libbpf-dev: yes" || echo "libbpf-dev: no"

clean:
	rm -rf $(BUILD_DIR)
