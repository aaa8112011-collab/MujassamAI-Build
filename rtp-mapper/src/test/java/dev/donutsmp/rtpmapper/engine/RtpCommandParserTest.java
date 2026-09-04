package dev.donutsmp.rtpmapper.engine;

import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;

import java.util.stream.Stream;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

final class RtpCommandParserTest {
	static Stream<Arguments> validCommands() {
		return Stream.of(
				Arguments.of("rtp east", RtpRegion.EAST),
				Arguments.of("/rtp west", RtpRegion.WEST),
				Arguments.of("  RTP   EU CENTRAL  ", RtpRegion.EU_CENTRAL),
				Arguments.of("rtp eu west", RtpRegion.EU_WEST),
				Arguments.of("rtp ASIA", RtpRegion.ASIA),
				Arguments.of("rtp oceania", RtpRegion.OCEANIA));
	}

	@ParameterizedTest
	@MethodSource("validCommands")
	void parsesSupportedManualCommands(String command, RtpRegion expected) {
		assertSame(expected, RtpCommandParser.parseRegion(command));
	}

	@ParameterizedTest
	@NullAndEmptySource
	@ValueSource(strings = {" ", "rtp", "rtp mars", "say /rtp east", "rtp east extra",
			"/ rtp east", "rtp eu-west", "rtp oceanic", "rtp east\nsay op me"})
	void rejectsAnythingThatIsNotOneExactRtpCommand(String command) {
		assertNull(RtpCommandParser.parseRegion(command));
	}
}
