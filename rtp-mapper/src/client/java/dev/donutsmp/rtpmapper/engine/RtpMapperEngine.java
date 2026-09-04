package dev.donutsmp.rtpmapper.engine;

import dev.donutsmp.rtpmapper.config.ConfigManager;
import dev.donutsmp.rtpmapper.config.MapperConfig;
import dev.donutsmp.rtpmapper.data.RtpSample;
import dev.donutsmp.rtpmapper.data.SampleStore;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;

import java.time.Duration;
import java.time.Instant;
import java.util.EnumSet;
import java.util.Locale;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Passive RTP observer. This class never sends commands or changes player input.
 * A sample is armed only after the player manually sends a recognized /rtp command.
 */
public final class RtpMapperEngine {
	private static final RtpMapperEngine INSTANCE = new RtpMapperEngine();

	private final SampleStore sampleStore = SampleStore.get();

	private RtpMapperState state = RtpMapperState.STOPPED;
	private boolean running;
	private boolean hasStartedSession;
	private Instant sessionStartedAt = Instant.now();
	private Instant sessionEndedAt = Instant.now();
	private Instant armExpiresAt = Instant.EPOCH;
	private Instant toastExpiresAt = Instant.EPOCH;

	private String statusMessage = "Stopped";
	private String toastMessage = "";
	private String serverStatus = "Not connected";

	private double preRtpX;
	private double preRtpZ;
	private double candidateLandingX;
	private double candidateLandingZ;
	private double lastRtpX;
	private double lastRtpZ;

	private int failedAttempts;
	private int stabilizeTicksRemaining;
	private RtpRegion requestedRegion;
	private RtpRegion suggestedRegion = RtpRegion.EAST;
	private LocalPlayer armedPlayer;
	private ClientLevel armedLevel;

	private Consumer<RtpSample> sampleListener = sample -> {};

	private RtpMapperEngine() {
	}

	public static RtpMapperEngine get() {
		return INSTANCE;
	}

	public void setSampleListener(Consumer<RtpSample> listener) {
		sampleListener = listener == null ? sample -> {} : listener;
	}

	public void start() {
		if (running) {
			return;
		}

		if (!isOnDonutSmp()) {
			showToast("Connect to DonutSMP before starting.");
			return;
		}

		running = true;
		hasStartedSession = true;
		sampleStore.startSession();
		sessionStartedAt = Instant.now();
		sessionEndedAt = sessionStartedAt;
		failedAttempts = 0;
		clearAttempt();
		suggestedRegion = nextEnabledRegion(null);
		state = RtpMapperState.OBSERVING;
		statusMessage = "Waiting for your manual /rtp command";
		showToast("Passive mapping started");
	}

	public void stop() {
		if (running) {
			sessionEndedAt = Instant.now();
		}
		running = false;
		clearAttempt();
		state = RtpMapperState.STOPPED;
		statusMessage = "Stopped";
		showToast("Mapping stopped");
	}

	public void onDisconnect() {
		if (running) {
			sessionEndedAt = Instant.now();
		}
		running = false;
		clearAttempt();
		state = RtpMapperState.STOPPED;
		statusMessage = "Disconnected — start mapping again after reconnecting";
		serverStatus = "Not connected";
	}

	public void toggleRunning() {
		if (running) {
			stop();
		} else {
			start();
		}
	}

	public void tick() {
		Minecraft minecraft = Minecraft.getInstance();
		updateServerStatus(minecraft);

		if (!running) {
			return;
		}

		if (minecraft.getCurrentServer() == null) {
			stop();
			showToast("Disconnected — mapping did not resume.");
			return;
		}

		LocalPlayer player = minecraft.player;
		if (player == null || minecraft.level == null) {
			if (isRequestArmed()) {
				cancelAttempt("Player or world changed; armed sample cancelled");
			}
			return;
		}

		if (!isOnDonutSmp()) {
			stop();
			showToast("Left DonutSMP — mapping did not resume.");
			return;
		}

		if (isRequestArmed()
				&& (player != armedPlayer || minecraft.level != armedLevel || !player.isAlive())) {
			cancelAttempt("Respawn or world change detected; armed sample cancelled");
			return;
		}

		switch (state) {
			case AWAITING_TELEPORT -> tickAwaitingTeleport(player);
			case STABILIZING_LANDING -> tickStabilizingLanding(player);
			case RECORDING_SAMPLE -> recordSample(player);
			default -> {
			}
		}
	}

	/**
	 * Called by Fabric's outgoing-command event. The command has no leading slash.
	 * The event is observational: it does not block, rewrite, or send the command.
	 */
	public void onUserCommand(String command) {
		if (!running) {
			return;
		}

		if (isRequestArmed()) {
			cancelAttempt("Another command was sent; armed sample cancelled");
			return;
		}

		RtpRegion region = RtpCommandParser.parseRegion(command);
		if (region == null) {
			return;
		}

		Minecraft minecraft = Minecraft.getInstance();
		LocalPlayer player = minecraft.player;
		if (player == null || minecraft.level == null || !player.isAlive()) {
			showToast("Could not arm RTP sample: player unavailable.");
			return;
		}

		requestedRegion = region;
		preRtpX = player.getX();
		preRtpZ = player.getZ();
		armedPlayer = player;
		armedLevel = minecraft.level;
		armExpiresAt = Instant.now().plusSeconds(
				ConfigManager.get().getConfig().teleportConfirmTimeoutSeconds
		);
		state = RtpMapperState.AWAITING_TELEPORT;
		statusMessage = "Waiting for " + region.displayName() + " landing";
		showToast("Armed one sample: " + region.displayName());
	}

	private void tickAwaitingTeleport(LocalPlayer player) {
		double dx = player.getX() - preRtpX;
		double dz = player.getZ() - preRtpZ;
		double distanceSquared = dx * dx + dz * dz;
		int threshold = ConfigManager.get().getConfig().teleportConfirmBlocks;

		if (distanceSquared >= (long) threshold * threshold) {
			candidateLandingX = player.getX();
			candidateLandingZ = player.getZ();
			stabilizeTicksRemaining = ConfigManager.get().getConfig().landingStabilizeTicks;
			state = RtpMapperState.STABILIZING_LANDING;
			statusMessage = "Landing detected — stabilizing";
			return;
		}

		if (Instant.now().isAfter(armExpiresAt)) {
			handleFailure("RTP landing was not detected in time");
		}
	}

	private void tickStabilizingLanding(LocalPlayer player) {
		if (Instant.now().isAfter(armExpiresAt)) {
			handleFailure("RTP landing did not stabilize in time");
			return;
		}

		double dx = player.getX() - candidateLandingX;
		double dz = player.getZ() - candidateLandingZ;
		if (dx * dx + dz * dz > 0.25) {
			candidateLandingX = player.getX();
			candidateLandingZ = player.getZ();
			stabilizeTicksRemaining = ConfigManager.get().getConfig().landingStabilizeTicks;
			statusMessage = "Landing moving — stabilizing";
			return;
		}

		if (--stabilizeTicksRemaining <= 0) {
			state = RtpMapperState.RECORDING_SAMPLE;
		} else {
			statusMessage = "Landing detected — stabilizing";
		}
	}

	private void recordSample(LocalPlayer player) {
		if (requestedRegion == null) {
			state = RtpMapperState.OBSERVING;
			return;
		}

		RtpRegion completedRegion = requestedRegion;
		RtpSample sample = RtpSample.create(
				sampleStore.getCurrentSessionId(),
				player.getX(),
				player.getY(),
				player.getZ(),
				completedRegion
		);

		boolean autoSave = ConfigManager.get().getConfig().autoSaveAfterSample;
		boolean persisted = !autoSave;
		if (autoSave) {
			try {
				sampleStore.appendSample(sample);
				persisted = true;
			} catch (Exception exception) {
				persisted = false;
			}
		}

		lastRtpX = sample.x();
		lastRtpZ = sample.z();
		sampleStore.addSample(sample);
		suggestedRegion = nextEnabledRegion(completedRegion);
		clearAttempt();
		state = RtpMapperState.OBSERVING;
		try {
			sampleListener.accept(sample);
		} catch (RuntimeException ignored) {
			// A UI listener must not leave the engine recording the same sample every tick.
		}

		if (autoSave && !persisted) {
			statusMessage = "Sample recorded in memory, but saving failed";
			showToast("Sample recorded, but disk save failed");
		} else {
			statusMessage = "Sample recorded; waiting for your next /rtp";
			String verb = autoSave ? "saved" : "recorded in memory";
			showToast("Sample #" + sampleStore.getSessionSamples().size() + " " + verb
					+ " [" + completedRegion.displayName() + "]");
		}
	}

	public void onChatMessage(String message) {
		handlePossibleFailureMessage(message);
	}

	public void onActionBarMessage(String message) {
		handlePossibleFailureMessage(message);
	}

	private void handlePossibleFailureMessage(String message) {
		if (!isRequestArmed()) {
			return;
		}

		String lower = message.toLowerCase(Locale.ROOT);
		boolean failed = lower.contains("rtp failed")
				|| lower.contains("teleport failed")
				|| lower.contains("teleport was cancelled")
				|| lower.contains("teleport was canceled")
				|| lower.contains("warmup cancelled")
				|| lower.contains("warmup canceled")
				|| lower.contains("could not teleport")
				|| lower.contains("unable to teleport");
		if (failed) {
			handleFailure("Server reported that RTP was cancelled or failed");
		}
	}

	private void handleFailure(String reason) {
		failedAttempts++;
		clearAttempt();
		state = running ? RtpMapperState.OBSERVING : RtpMapperState.STOPPED;
		statusMessage = reason;
		showToast(reason);
	}

	private void cancelAttempt(String reason) {
		clearAttempt();
		state = running ? RtpMapperState.OBSERVING : RtpMapperState.STOPPED;
		statusMessage = reason;
		showToast(reason);
	}

	private void clearAttempt() {
		requestedRegion = null;
		armedPlayer = null;
		armedLevel = null;
		armExpiresAt = Instant.EPOCH;
		stabilizeTicksRemaining = 0;
	}

	private RtpRegion nextEnabledRegion(RtpRegion after) {
		MapperConfig config = ConfigManager.get().getConfig();
		Set<RtpRegion> enabled = EnumSet.noneOf(RtpRegion.class);
		for (String value : config.enabledRegions) {
			RtpRegion parsed = RtpRegion.fromIdOrNull(value);
			if (parsed != null) {
				enabled.add(parsed);
			}
		}
		if (enabled.isEmpty()) {
			enabled = EnumSet.allOf(RtpRegion.class);
		}

		RtpRegion[] regions = RtpRegion.values();
		int start = after == null ? -1 : after.ordinal();
		for (int offset = 1; offset <= regions.length; offset++) {
			RtpRegion candidate = regions[(start + offset) % regions.length];
			if (enabled.contains(candidate)) {
				return candidate;
			}
		}
		return RtpRegion.EAST;
	}

	public static RtpRegion parseRtpRegion(String command) {
		return RtpCommandParser.parseRegion(command);
	}

	private void updateServerStatus(Minecraft minecraft) {
		if (minecraft.getCurrentServer() == null) {
			serverStatus = "Not connected";
			return;
		}

		String address = minecraft.getCurrentServer().ip;
		serverStatus = isOnDonutSmp()
				? "Configured server accepted"
				: "Connected to " + address;
	}

	public boolean isOnDonutSmp() {
		Minecraft minecraft = Minecraft.getInstance();
		if (minecraft.getCurrentServer() == null) {
			return false;
		}

		return ServerHostMatcher.matches(
				minecraft.getCurrentServer().ip,
				ConfigManager.get().getConfig().serverHost);
	}

	public boolean isRunning() {
		return running;
	}

	public boolean isRequestArmed() {
		return state == RtpMapperState.AWAITING_TELEPORT
				|| state == RtpMapperState.STABILIZING_LANDING
				|| state == RtpMapperState.RECORDING_SAMPLE;
	}

	public RtpMapperState getState() {
		return state;
	}

	public String getStatusMessage() {
		return statusMessage;
	}

	public String getToastMessage() {
		return Instant.now().isAfter(toastExpiresAt) ? "" : toastMessage;
	}

	public String getServerStatus() {
		return serverStatus;
	}

	public String getRequestedRegionLabel() {
		return requestedRegion == null ? "None" : requestedRegion.displayName();
	}

	public String getSuggestedRegionLabel() {
		return suggestedRegion.displayName();
	}

	public String getSuggestedCommand() {
		return "/rtp " + suggestedRegion.commandArgument();
	}

	public int getFailedAttempts() {
		return failedAttempts;
	}

	public double getLastRtpX() {
		return lastRtpX;
	}

	public double getLastRtpZ() {
		return lastRtpZ;
	}

	public Duration getSessionDuration() {
		if (!hasStartedSession) {
			return Duration.ZERO;
		}
		return Duration.between(sessionStartedAt, running ? Instant.now() : sessionEndedAt);
	}

	public Duration getArmTimeRemaining() {
		if (state != RtpMapperState.AWAITING_TELEPORT
				&& state != RtpMapperState.STABILIZING_LANDING) {
			return Duration.ZERO;
		}
		Duration remaining = Duration.between(Instant.now(), armExpiresAt);
		return remaining.isNegative() ? Duration.ZERO : remaining;
	}

	public String getNextActionLabel() {
		return switch (state) {
			case STOPPED -> "Start passive mapping";
			case OBSERVING -> getSuggestedCommand();
			case AWAITING_TELEPORT -> "Waiting " + formatCountdown(getArmTimeRemaining());
			case STABILIZING_LANDING -> "Stabilizing…";
			case RECORDING_SAMPLE -> "Saving…";
		};
	}

	public static String formatCountdown(Duration duration) {
		long totalSeconds = Math.max(0, duration.getSeconds());
		long hours = totalSeconds / 3600;
		long minutes = (totalSeconds % 3600) / 60;
		long seconds = totalSeconds % 60;
		return String.format(Locale.ROOT, "%02d:%02d:%02d", hours, minutes, seconds);
	}

	public static String formatDuration(Duration duration) {
		return formatCountdown(duration);
	}

	private void showToast(String message) {
		toastMessage = message;
		toastExpiresAt = Instant.now().plusSeconds(4);
	}
}
