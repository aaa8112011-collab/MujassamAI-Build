package dev.donutsmp.rtpmapper.render;

import dev.donutsmp.rtpmapper.config.ConfigManager;
import dev.donutsmp.rtpmapper.data.RtpSample;
import dev.donutsmp.rtpmapper.data.SampleStore;
import dev.donutsmp.rtpmapper.engine.RtpMapperEngine;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;

import java.util.Locale;

public final class MapperHudOverlay {
	private static final MapRenderer MINI_MAP = new MapRenderer();

	private MapperHudOverlay() {
	}

	public static void render(GuiGraphics graphics) {
		var config = ConfigManager.get().getConfig();
		if (!config.hudEnabled) {
			return;
		}

		RtpMapperEngine engine = RtpMapperEngine.get();
		Minecraft minecraft = Minecraft.getInstance();
		if (minecraft.player == null) {
			return;
		}

		int panelWidth = 250;
		int panelHeight = config.hudShowMiniMap ? 194 : 108;
		int margin = 8;
		int x = margin;
		int y = margin;

		switch (config.hudCorner) {
			case "top_right" -> x = minecraft.getWindow().getGuiScaledWidth() - panelWidth - margin;
			case "bottom_left" -> y = minecraft.getWindow().getGuiScaledHeight() - panelHeight - margin;
			case "bottom_right" -> {
				x = minecraft.getWindow().getGuiScaledWidth() - panelWidth - margin;
				y = minecraft.getWindow().getGuiScaledHeight() - panelHeight - margin;
			}
			default -> {
			}
		}

		graphics.fill(x, y, x + panelWidth, y + panelHeight, 0xE6101824);
		drawOutline(graphics, x, y, panelWidth, panelHeight, 0xFF355068);

		int textY = y + 6;
		int textX = x + 8;
		int statusColor = engine.isRunning() ? 0xFF66BB6A : 0xFFE57373;
		String status = engine.isRunning() ? "RECORDING" : "STOPPED";

		graphics.drawString(minecraft.font, "DonutSMP RTP Mapper", textX, textY, 0xFFECEFF1, false);
		graphics.drawString(minecraft.font, status, x + panelWidth - 64, textY, statusColor, false);
		textY += 12;
		graphics.drawString(minecraft.font, "Passive observer — commands stay manual",
				textX, textY, 0xFF80CBC4, false);
		textY += 11;

		SampleStore store = SampleStore.get();
		graphics.drawString(minecraft.font,
				"Samples: " + store.getSessionSamples().size() + " session / "
						+ store.getAllSamples().size() + " total",
				textX, textY, 0xFFB0BEC5, false);
		textY += 10;

		var player = minecraft.player;
		graphics.drawString(minecraft.font, String.format(Locale.ROOT,
				"Current X/Y/Z: %.0f / %.0f / %.0f", player.getX(), player.getY(), player.getZ()
		), textX, textY, 0xFFB0BEC5, false);
		textY += 10;

		if (store.getSessionSamples().isEmpty()) {
			graphics.drawString(minecraft.font, "Last RTP: —", textX, textY, 0xFFB0BEC5, false);
		} else {
			RtpSample last = store.getSessionSamples().getLast();
			graphics.drawString(minecraft.font, String.format(Locale.ROOT,
					"Last: %.0f / %.0f [%s]", last.x(), last.z(), last.requestedRegionDisplayName()
			), textX, textY, 0xFFB0BEC5, false);
		}
		textY += 10;

		graphics.drawString(minecraft.font, "Armed region: " + engine.getRequestedRegionLabel(),
				textX, textY, 0xFFB0BEC5, false);
		textY += 10;
		graphics.drawString(minecraft.font, "Suggested next: " + engine.getSuggestedCommand(),
				textX, textY, 0xFFFFD54F, false);

		String toast = engine.getToastMessage();
		if (!toast.isBlank()) {
			textY += 10;
			graphics.drawString(minecraft.font, toast, textX, textY, 0xFF81C784, false);
		}

		if (config.hudShowMiniMap) {
			int mapX = x + 8;
			int mapY = y + panelHeight - 88;
			MINI_MAP.render(graphics, mapX, mapY, panelWidth - 16, 80, store.getSessionSamples());
		}
	}

	private static void drawOutline(GuiGraphics graphics, int x, int y, int width, int height, int color) {
		graphics.fill(x, y, x + width, y + 1, color);
		graphics.fill(x, y + height - 1, x + width, y + height, color);
		graphics.fill(x, y, x + 1, y + height, color);
		graphics.fill(x + width - 1, y, x + width, y + height, color);
	}
}
