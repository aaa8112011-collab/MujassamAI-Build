package dev.donutsmp.rtpmapper.engine;

/** Parses only one complete, manually entered regional RTP command. */
public final class RtpCommandParser {
	private RtpCommandParser() {
	}

	public static RtpRegion parseRegion(String command) {
		if (command == null || command.indexOf('\n') >= 0 || command.indexOf('\r') >= 0) {
			return null;
		}

		String normalized = command.trim();
		if (normalized.startsWith("/")) {
			if (normalized.length() == 1 || Character.isWhitespace(normalized.charAt(1))) {
				return null;
			}
			normalized = normalized.substring(1).trim();
		}

		String[] parts = normalized.split("\\s+", 2);
		if (parts.length != 2 || !parts[0].equalsIgnoreCase("rtp")) {
			return null;
		}
		return RtpRegion.fromCommandArgument(parts[1]);
	}
}
