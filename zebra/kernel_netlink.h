// SPDX-License-Identifier: GPL-2.0-or-later
/* Declarations and definitions for kernel interaction over netlink
 * Copyright (C) 2016 Cumulus Networks, Inc.
 */

#ifndef _ZEBRA_KERNEL_NETLINK_H
#define _ZEBRA_KERNEL_NETLINK_H

#ifdef HAVE_NETLINK

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>
#include <linux/netlink.h>

#include "lib/ns.h"

#ifdef __cplusplus
extern "C" {
#endif

struct nlsock;
struct rtattr;
struct rtnexthop;
struct vty;
struct zebra_dplane_ctx;
struct zebra_dplane_info;
struct zebra_ns;

/* FPM-specific extension values, for messages and attributes */
typedef enum {
	/*
	 * FRR and SWSS can build with kernel headers that define different
	 * RTM_MAX values, so this private ABI must use a fixed base.
	 */
	RTM_FPM_FIRST = 140,
	RTM_FPM_GETENCAPS = RTM_FPM_FIRST,
	RTM_FPM_ADDENCAP,
	RTM_FPM_DELENCAP,
	RTM_FPM_ADD_EVPN_SHL,
	RTM_FPM_DEL_EVPN_SHL,
	RTM_FPM_ADD_EVPN_DF,
	RTM_FPM_DEL_EVPN_DF,
	RTM_FPM_ADD_EVPN_ES_BACKUP_NHG,
	RTM_FPM_DEL_EVPN_ES_BACKUP_NHG,
	RTM_FPM_LAST = RTM_FPM_DEL_EVPN_ES_BACKUP_NHG,
	RTM_FPM_MAX = RTM_FPM_LAST
} rtm_fpm_msg_types_et;

struct evpn_shl_msg {
	int esm_ifindex;
	uint16_t esm_vid;
};

struct evpn_df_msg {
	int edm_ifindex;
	uint16_t edm_vid;
	int edm_non_df;
};

struct evpn_backup_nhg_msg {
	int ebnm_ifindex;
	int ebnm_backup_nhg_id;
};

#define FPM_SHL_IPV4_ADDR 1
#define FPM_SHL_IPV6_ADDR 2
#define FPM_SHL_LAST FPM_SHL_IPV6_ADDR
#define FPM_SHL_MAX FPM_SHL_LAST

#define NL_RCV_PKT_BUF_SIZE     (34 * 1024)
#define NL_PKT_BUF_SIZE         8192

extern void netlink_parse_rtattr_flags(struct rtattr **tb, int max,
				 struct rtattr *rta, int len,
				 unsigned short flags);

/* Parse attributes carried in a message. */
void netlink_parse_rtattr_msg(struct rtattr **tb, int max,
			      const struct nlmsghdr *msg);
extern const char *nl_msg_type_to_str(uint16_t msg_type);
extern const char *nl_rtproto_to_str(uint8_t rtproto);
extern const char *nl_family_to_str(uint8_t family);
extern const char *nl_rttype_to_str(uint8_t rttype);

typedef int (*netlink_parse_filter_t)(struct nlmsghdr *h, ns_id_t ns_id, int startup, void *arg);

extern int netlink_parse_info(netlink_parse_filter_t filter, struct nlsock *nl,
			      const struct zebra_dplane_info *dp_info, int count, bool startup,
			      void *arg, int *nl_err);
extern int netlink_talk_filter(struct nlmsghdr *h, ns_id_t ns, int startup, void *arg);
extern int netlink_talk(netlink_parse_filter_t filter, struct nlmsghdr *n, struct nlsock *nl,
			struct zebra_ns *zns, bool startup, void *arg, int *nl_err);
extern int ge_netlink_talk(netlink_parse_filter_t filter, struct nlmsghdr *n, struct zebra_ns *zns,
			   bool startup, void *arg, int *nl_err);
extern int netlink_request(struct nlsock *nl, void *req);

enum netlink_msg_status {
	FRR_NETLINK_SUCCESS,
	FRR_NETLINK_ERROR,
	FRR_NETLINK_QUEUED,
};

struct nl_batch;

/*
 * netlink_batch_add_msg - add message to the netlink batch using dplane
 * context object.
 *
 * @ctx:         Dataplane context
 * @msg_encoder: A function that encodes dplane context object into
 *               netlink message. Should take dplane context object,
 *               pointer to a buffer and buffer's length as parameters
 *               and should return -1 on error, 0 on buffer overflow or
 *               size of the encoded message.
 * @ignore_res:  Whether the result of this message should be ignored.
 *               This should be used in some 'update' cases where we
 *               need to send two messages for one context object.
 *
 * Return:		Status of the message.
 */
extern enum netlink_msg_status netlink_batch_add_msg(
	struct nl_batch *bth, struct zebra_dplane_ctx *ctx,
	ssize_t (*msg_encoder)(struct zebra_dplane_ctx *, void *, size_t),
	bool ignore_res);

/*
 * Vty/cli apis
 */
extern int netlink_config_write_helper(struct vty *vty);

/*
 * Configure size of the batch buffer and sending threshold. If 'unset', reset
 * to default value.
 */
extern void netlink_set_batch_buffer_size(uint32_t size, uint32_t threshold,
					  bool set);

extern struct nlsock *kernel_netlink_nlsock_lookup(int sock);

#ifdef __cplusplus
}
#endif

#endif /* HAVE_NETLINK */

#endif /* _ZEBRA_KERNEL_NETLINK_H */
