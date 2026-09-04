package dev.donutsmp.rtpmapper.client.ui;

import dev.donutsmp.rtpmapper.config.ConfigManager;
import dev.donutsmp.rtpmapper.data.RtpSample;
import dev.donutsmp.rtpmapper.data.SampleStore;
import dev.donutsmp.rtpmapper.engine.RtpMapperEngine;
import dev.donutsmp.rtpmapper.engine.RtpRegion;
import dev.donutsmp.rtpmapper.render.MapRenderer;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;

import java.nio.file.Path;
import java.util.EnumMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public final class MapperScreen extends Screen {
	private final MapRenderer mapRenderer = new MapRenderer();
	private boolean showSessionOnly = true;
	private String footerMessage = "Ready — press Start Mapping, then type /rtp <region> yourself";
	private Button startStopButton;
	private Button viewButton;
	private boolean draggingMap;

	public MapperScreen() {
		super(Component.literal("DonutSMP RTP Mapper"));
	}

	@Override
	protected void init() {
		int top = 8;
		int buttonWidth = 92;
		int gap = 4;
		int x = 8;

		startStopButton = addRenderableWidget(Button.builder(startStopLabel(), button -> toggleMapping())
				.bounds(x, top, buttonWidth, 20).build());
		x += buttonWidth + gap;
		addRenderableWidget(Button.builder(Component.literal("Clear Session"), button -> clearData())
				.bounds(x, top, buttonWidth, 20).build());
		x += buttonWidth + gap;
		addRenderableWidget(Button.builder(Component.literal("Export CSV"), button -> exportCsv())
				.bounds(x, top, buttonWidth, 20).build());
		x += buttonWidth + gap;
		addRenderableWidget(Button.builder(Component.literal("Reset View"), button -> mapRenderer.resetView())
				.bounds(x, top, buttonWidth, 20).build());
		x += buttonWidth + gap;
		addRenderableWidget(Button.builder(Component.literal("Settings"), button -> openSettings())
				.bounds(x, top, buttonWidth, 20).build());

		viewButton = addRenderableWidget(Button.builder(viewLabel(), button -> {
			showSessionOnly = !showSessionOnly;
			viewButton.setMessage(viewLabel());
		}).bounds(width - 110, top, 102, 20).build());
	}

	private Component startStopLabel() {
		return Component.literal(RtpMapperEngine.get().isRunning() ? "Stop Mapping" : "Start Mapping");
	}

	private Component viewLabel() {
		return Component.literal(showSessionOnly ? "View: Session" : "View: All");
	}

	@Override
	public void render(GuiGraphics graphics, int mouseX, int mouseY, float partialTick) {
		renderBackground(graphics, mouseX, mouseY, partialTick);

		RtpMapperEngine engine = RtpMapperEngine.get();
		SampleStore store = SampleStore.get();
		Minecraft minecraft = Minecraft.getInstance();

		int panelWidth = 236;
		int topBarHeight = 58;
		int bottomBarHeight = 18;
		int mapX = panelWidth + 16;
		int mapY = topBarHeight + 8;
		int mapWidth = Math.max(100, width - mapX - 8);
		int mapHeight = Math.max(100, height - mapY - bottomBarHeight - 8);

		graphics.fill(0, 0, width, topBarHeight, 0xF0111822);
		graphics.drawString(font, "DonutSMP RTP Mapper", 8, 34, 0xFFECEFF1, false);

		String status = !engine.isRunning()
				? "STOPPED"
				: engine.isRequestArmed() ? "ARMED" : "OBSERVING";
		int statusColor = !engine.isRunning()
				? 0xFFE57373
				: engine.isRequestArmed() ? 0xFFFFD54F : 0xFF66BB6A;
		graphics.drawString(font, status, 150, 34, statusColor, false);
		graphics.drawCenteredString(font,
				"Server chooses every coordinate; this mapper cannot guarantee a new or unique landing.",
				width / 2, 46, 0xFFFFB74D);

		graphics.fill(8, topBarHeight + 8, panelWidth, height - bottomBarHeight - 8, 0xE6101824);
		renderStatusPanel(graphics, engine, store, minecraft, 16, topBarHeight + 16);

		List<RtpSample> samples = showSessionOnly ? store.getSessionSamples() : store.getAllSamples();
		String mapTitle = showSessionOnly
				? "Random Teleports on DonutSMP — Session (" + samples.size() + ")"
				: "Random Teleports on DonutSMP — All (" + samples.size() + ")";
		graphics.drawString(font, mapTitle, mapX, mapY - 12, 0xFFCFD8DC, false);
		mapRenderer.render(graphics, mapX, mapY, mapWidth, mapHeight, samples);
		graphics.drawString(font, "Drag to pan • mouse wheel to zoom", mapX + 6,
				mapY + mapHeight - 12, 0xFF78909C, false);

		renderStatistics(graphics, samples, 16, topBarHeight + 142);

		String toast = engine.getToastMessage();
		if (!toast.isBlank()) {
			footerMessage = toast;
		}
		graphics.drawString(font, footerMessage, 8, height - 14, 0xFF90A4AE, false);

		super.render(graphics, mouseX, mouseY, partialTick);
	}

	private void renderStatusPanel(GuiGraphics graphics, RtpMapperEngine engine, SampleStore store,
			Minecraft minecraft, int x, int y) {
		int line = y;
		graphics.drawString(font, "MAPPER STATUS", x, line, 0xFF90CAF9, false);
		line += 14;
		graphics.drawString(font,
				"Samples: " + store.getSessionSamples().size() + " session / "
						+ store.getAllSamples().size() + " total",
				x, line, 0xFFB0BEC5, false);
		line += 11;

		if (minecraft.player != null) {
			graphics.drawString(font, String.format(Locale.ROOT,
					"Current X/Y/Z: %.0f / %.0f / %.0f",
					minecraft.player.getX(), minecraft.player.getY(), minecraft.player.getZ()
			), x, line, 0xFFB0BEC5, false);
			line += 11;
		}

		if (store.getSessionSamples().isEmpty()) {
			graphics.drawString(font, "Last RTP X/Z: —", x, line, 0xFFB0BEC5, false);
		} else {
			RtpSample last = store.getSessionSamples().getLast();
			graphics.drawString(font, String.format(Locale.ROOT,
					"Last RTP X/Z: %.0f / %.0f", last.x(), last.z()
			), x, line, 0xFFB0BEC5, false);
		}
		line += 11;

		graphics.drawString(font, "Armed: " + engine.getRequestedRegionLabel(),
				x, line, 0xFFB0BEC5, false);
		line += 11;
		graphics.drawString(font, "Suggested: " + engine.getSuggestedCommand(),
				x, line, 0xFFFFD54F, false);
		line += 11;
		graphics.drawString(font, "Next: " + engine.getNextActionLabel(),
				x, line, 0xFFB0BEC5, false);
		line += 11;
		graphics.drawString(font,
				"Session: " + RtpMapperEngine.formatDuration(engine.getSessionDuration())
						+ "  Failed: " + engine.getFailedAttempts(),
				x, line, 0xFFB0BEC5, false);
		line += 11;
		graphics.drawString(font, engine.getServerStatus(), x, line, 0xFF81C784, false);
	}

	private void renderStatistics(GuiGraphics graphics, List<RtpSample> samples, int x, int y) {
		graphics.drawString(font, "STATISTICS", x, y, 0xFF90CAF9, false);
		y += 14;
		if (samples.isEmpty()) {
			graphics.drawString(font, "No samples in this view.", x, y, 0xFF78909C, false);
			return;
		}

		double minX = samples.stream().mapToDouble(RtpSample::x).min().orElse(0);
		double maxX = samples.stream().mapToDouble(RtpSample::x).max().orElse(0);
		double minZ = samples.stream().mapToDouble(RtpSample::z).min().orElse(0);
		double maxZ = samples.stream().mapToDouble(RtpSample::z).max().orElse(0);
		double avgDistance = samples.stream().mapToDouble(RtpSample::distanceFromOrigin).average().orElse(0);

		graphics.drawString(font, String.format(Locale.ROOT, "X range: %.0f .. %.0f", minX, maxX),
				x, y, 0xFFB0BEC5, false);
		y += 10;
		graphics.drawString(font, String.format(Locale.ROOT, "Z range: %.0f .. %.0f", minZ, maxZ),
				x, y, 0xFFB0BEC5, false);
		y += 10;
		graphics.drawString(font, String.format(Locale.ROOT, "Mean radius: %.0f", avgDistance),
				x, y, 0xFFB0BEC5, false);
		y += 13;

		long ne = samples.stream().filter(sample -> sample.x() >= 0 && sample.z() < 0).count();
		long nw = samples.stream().filter(sample -> sample.x() < 0 && sample.z() < 0).count();
		long se = samples.stream().filter(sample -> sample.x() >= 0 && sample.z() >= 0).count();
		long sw = samples.stream().filter(sample -> sample.x() < 0 && sample.z() >= 0).count();
		graphics.drawString(font, "QUADRANTS", x, y, 0xFF90CAF9, false);
		y += 11;
		graphics.drawString(font, String.format(Locale.ROOT,
				"NE %d%%   NW %d%%", percent(ne, samples.size()), percent(nw, samples.size())),
				x, y, 0xFFB0BEC5, false);
		y += 10;
		graphics.drawString(font, String.format(Locale.ROOT,
				"SE %d%%   SW %d%%", percent(se, samples.size()), percent(sw, samples.size())),
				x, y, 0xFFB0BEC5, false);
		y += 13;

		Map<RtpRegion, Long> regionCounts = new EnumMap<>(RtpRegion.class);
		for (RtpRegion region : RtpRegion.values()) {
			regionCounts.put(region, 0L);
		}
		for (RtpSample sample : samples) {
			RtpRegion region = RtpRegion.fromIdOrNull(sample.requestedRegion());
			if (region != null) {
				regionCounts.put(region, regionCounts.get(region) + 1);
			}
		}
		graphics.drawString(font, "REQUESTED REGIONS", x, y, 0xFF90CAF9, false);
		y += 11;
		for (int index = 0; index < RtpRegion.values().length; index += 2) {
			RtpRegion left = RtpRegion.values()[index];
			RtpRegion right = RtpRegion.values()[index + 1];
			graphics.drawString(font,
					left.displayName() + " " + regionCounts.get(left),
					x, y, MapRenderer.colorForRegion(left.id()), false);
			graphics.drawString(font,
					right.displayName() + " " + regionCounts.get(right),
					x + 112, y, MapRenderer.colorForRegion(right.id()), false);
			y += 10;
		}
		y += 3;

		graphics.drawString(font, "RADIAL BUCKETS", x, y, 0xFF90CAF9, false);
		y += 11;
		long under50 = samples.stream().filter(sample -> sample.distanceFromOrigin() < 50_000).count();
		long under100 = samples.stream().filter(sample -> sample.distanceFromOrigin() >= 50_000
				&& sample.distanceFromOrigin() < 100_000).count();
		long under200 = samples.stream().filter(sample -> sample.distanceFromOrigin() >= 100_000
				&& sample.distanceFromOrigin() < 200_000).count();
		long over200 = samples.stream().filter(sample -> sample.distanceFromOrigin() >= 200_000).count();
		graphics.drawString(font, "0–50k " + under50 + "    50–100k " + under100,
				x, y, 0xFFB0BEC5, false);
		y += 10;
		graphics.drawString(font, "100–200k " + under200 + "  200k+ " + over200,
				x, y, 0xFFB0BEC5, false);
	}

	private long percent(long count, int total) {
		return total == 0 ? 0 : Math.round(count * 100.0 / total);
	}

	private void toggleMapping() {
		RtpMapperEngine engine = RtpMapperEngine.get();
		engine.toggleRunning();
		startStopButton.setMessage(startStopLabel());
		footerMessage = engine.isRunning()
				? "Passive mapping active — you must type /rtp <region> yourself"
				: "Mapping stopped";
	}

	private void clearData() {
		SampleStore.get().clearSession();
		footerMessage = "Session samples cleared (saved master CSV was not deleted)";
	}

	private void exportCsv() {
		try {
			SampleStore store = SampleStore.get();
			List<RtpSample> samples = showSessionOnly ? store.getSessionSamples() : store.getAllSamples();
			Path target = ConfigManager.get().getConfigDir().resolve(
					showSessionOnly ? "export-session.csv" : "export-all.csv"
			);
			store.exportViewCsv(samples, target);
			footerMessage = "Exported CSV to " + target.getFileName();
		} catch (Exception exception) {
			footerMessage = "Export failed: " + exception.getMessage();
		}
	}

	private void openSettings() {
		Minecraft.getInstance().setScreen(new SettingsScreen(this));
	}

	@Override
	public boolean mouseClicked(MouseButtonEvent event, boolean doubled) {
		if (event.button() == 0 && mapRenderer.contains(event.x(), event.y())) {
			draggingMap = true;
			setDragging(true);
			return true;
		}
		return super.mouseClicked(event, doubled);
	}

	@Override
	public boolean mouseDragged(MouseButtonEvent event, double deltaX, double deltaY) {
		if (draggingMap && event.button() == 0) {
			mapRenderer.panPixels(deltaX, deltaY);
			return true;
		}
		return super.mouseDragged(event, deltaX, deltaY);
	}

	@Override
	public boolean mouseReleased(MouseButtonEvent event) {
		if (draggingMap && event.button() == 0) {
			draggingMap = false;
			setDragging(false);
			return true;
		}
		return super.mouseReleased(event);
	}

	@Override
	public boolean mouseScrolled(double mouseX, double mouseY, double horizontalAmount, double verticalAmount) {
		if (verticalAmount != 0.0 && mapRenderer.contains(mouseX, mouseY)) {
			mapRenderer.zoomAt(mouseX, mouseY, verticalAmount);
			return true;
		}
		return super.mouseScrolled(mouseX, mouseY, horizontalAmount, verticalAmount);
	}

	@Override
	public boolean isPauseScreen() {
		return false;
	}
}
