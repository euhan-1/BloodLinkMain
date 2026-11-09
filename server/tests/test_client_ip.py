"""client_ip.client_ip: which address a request came from, with the header behaviour documented in server/client_ip.py. Pure, no DB.

Addresses are real public ones because the function refuses private/reserved ranges on purpose (they would become one shared key)."""
import unittest

from starlette.datastructures import Headers

from client_ip import client_ip

VISITOR = "81.107.0.110"
VERCEL = "76.76.21.9"
CLOUDFLARE = "172.71.1.1"   # a public Cloudflare range, not RFC 1918
INTERNAL = "10.0.0.1"


def headers(**kw):
    return Headers({k.replace("_", "-"): v for k, v in kw.items()})


class ClientIpTests(unittest.TestCase):
    def test_a_request_through_vercel_is_the_visitor_not_vercels_shared_address(self):
        h = headers(true_client_ip=VERCEL, x_forwarded_for=f"{VISITOR}, {VERCEL}, {CLOUDFLARE}, {INTERNAL}")
        self.assertEqual(client_ip(h, INTERNAL), VISITOR)

    def test_the_leftmost_entry_is_never_taken_by_position(self):
        # something extra on the left (a client's own claim) must not win: the entry before the peer is what counts
        h = headers(true_client_ip=VERCEL, x_forwarded_for=f"9.9.9.9, {VISITOR}, {VERCEL}, {CLOUDFLARE}, {INTERNAL}")
        self.assertEqual(client_ip(h, INTERNAL), VISITOR)

    def test_it_does_not_count_hops_so_an_extra_proxy_on_the_right_changes_nothing(self):
        h = headers(true_client_ip=VERCEL, x_forwarded_for=f"{VISITOR}, {VERCEL}, {CLOUDFLARE}, {INTERNAL}, 10.9.9.9, 10.8.8.8")
        self.assertEqual(client_ip(h, INTERNAL), VISITOR)

    def test_a_caller_straight_to_render_with_no_forwarded_header_is_the_address_render_saw(self):
        h = headers(true_client_ip=VISITOR, x_forwarded_for=f"{VISITOR}, {CLOUDFLARE}, {INTERNAL}")
        self.assertEqual(client_ip(h, INTERNAL), VISITOR)  # the peer is the first entry: nothing before it to take
        self.assertEqual(client_ip(headers(true_client_ip=VISITOR), INTERNAL), VISITOR)

    def test_a_forwarded_header_alone_is_ignored(self):
        # without True-Client-IP there is no way to tell which entry a proxy wrote, so none is believed
        h = headers(x_forwarded_for="1.2.3.4, 5.6.7.8")
        self.assertIsNone(client_ip(h, INTERNAL))
        self.assertEqual(client_ip(h, VISITOR), VISITOR)  # only the connecting address (local development, no proxy)

    def test_a_direct_caller_can_choose_the_entry_before_the_peer_and_that_is_the_documented_limit(self):
        h = headers(true_client_ip=VISITOR, x_forwarded_for=f"1.2.3.4, {VISITOR}, {CLOUDFLARE}, {INTERNAL}")
        self.assertEqual(client_ip(h, INTERNAL), "1.2.3.4")  # the per-address counter can be dodged this way; the account counter cannot

    def test_addresses_that_are_not_plain_public_ips_become_unknown_never_a_shared_key(self):
        for h, peer in [
            (headers(true_client_ip=INTERNAL), None),                                   # a proxy's own address
            (headers(true_client_ip=VERCEL, x_forwarded_for=f"not-an-ip, {VERCEL}"), None),  # garbage before the peer
            (headers(true_client_ip=VERCEL, x_forwarded_for=f"192.168.1.5, {VERCEL}"), None),  # private before the peer
            (headers(true_client_ip="garbage"), None),
            (headers(), "127.0.0.1"),
            (headers(), None),
            (headers(), ""),
        ]:
            self.assertIsNone(client_ip(h, peer), (dict(h), peer))

    def test_a_plain_public_connecting_address_is_used_when_there_is_no_proxy(self):
        self.assertEqual(client_ip(headers(), VISITOR), VISITOR)
        self.assertEqual(client_ip(headers(), "2001:4860:4860::8888"), "2001:4860:4860::8888")
