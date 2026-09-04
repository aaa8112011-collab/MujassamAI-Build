package dev.donutsmp.rtpmapper.engine;

import java.util.Locale;

/** Exact host/subdomain matching that rejects suffix lookalikes such as donutsmp.net.example. */
public final class ServerHostMatcher {
	private ServerHostMatcher() {
	}

	public static boolean matches(String connectedAddress, String expectedHost) {
		String connected = normalize(connectedAddress);
		String expected = normalize(expectedHost);
		return !expected.isEmpty()
				&& (connected.equals(expected) || connected.endsWith("." + expected));
	}

	static String normalize(String address) {
		String normalized = address == null ? "" : address.trim().toLowerCase(Locale.ROOT);
		int scheme = normalized.indexOf("://");
		if (scheme >= 0) {
			normalized = normalized.substring(scheme + 3);
		}
		int slash = normalized.indexOf('/');
		if (slash >= 0) {
			normalized = normalized.substring(0, slash);
		}
		int colon = normalized.lastIndexOf(':');
		if (colon > 0 && normalized.indexOf(':') == colon) {
			normalized = normalized.substring(0, colon);
		}
		if (normalized.endsWith(".")) {
			normalized = normalized.substring(0, normalized.length() - 1);
		}
		return normalized;
	}
}
