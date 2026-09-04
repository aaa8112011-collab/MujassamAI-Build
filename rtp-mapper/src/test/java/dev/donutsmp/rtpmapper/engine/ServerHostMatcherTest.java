package dev.donutsmp.rtpmapper.engine;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class ServerHostMatcherTest {
	@Test
	void acceptsExactHostSubdomainsAndPorts() {
		assertTrue(ServerHostMatcher.matches("donutsmp.net", "donutsmp.net"));
		assertTrue(ServerHostMatcher.matches("play.donutsmp.net:25565", "donutsmp.net"));
		assertTrue(ServerHostMatcher.matches("DONUTSMP.NET.", "donutsmp.net"));
	}

	@Test
	void rejectsLookalikesAndEmptyAllowlist() {
		assertFalse(ServerHostMatcher.matches("donutsmp.net.example.org", "donutsmp.net"));
		assertFalse(ServerHostMatcher.matches("evildonutsmp.net", "donutsmp.net"));
		assertFalse(ServerHostMatcher.matches("donutsmp.net", ""));
		assertFalse(ServerHostMatcher.matches(null, "donutsmp.net"));
	}
}
