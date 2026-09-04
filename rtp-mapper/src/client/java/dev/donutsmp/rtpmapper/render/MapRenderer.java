package dev.donutsmp.rtpmapper.render;

import dev.donutsmp.rtpmapper.data.RtpSample;
import dev.donutsmp.rtpmapper.engine.RtpRegion;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;

import java.util.List;

public final class MapRenderer {
	public static final int[] RING_DISTANCES = {50_000, 100_000, 200_000, 300_000};

	private double panX;
	private double panZ;
	private double zoom = 1.0;
	private int mapX;
	private int mapY;
	private int mapWidth;
	private int mapHeight;

	public void resetView() {
		panX = 0.0;
		panZ = 0.0;
		zoom = 1.0;
	}

	public boolean contains(double x, double y) {
		return x >= mapX && x < mapX + mapWidth && y >= mapY && y < mapY + mapHeight;
	}

	public void panPixels(double deltaX, double deltaY) {
		panX += deltaX;
		panZ += deltaY;
	}

	public void zoomAt(double mouseX, double mouseY, double wheelAmount) {
		if (!contains(mouseX, mouseY) || wheelAmount == 0.0) {
			return;
		}
		double oldZoom = zoom;
		zoom = Math.clamp(zoom * Math.pow(1.2, wheelAmount), 0.25, 12.0);
		double ratio = zoom / oldZoom;
		double centerX = mapX + mapWidth / 2.0 + panX;
		double centerY = mapY + mapHeight / 2.0 + panZ;
		panX += (mouseX - centerX) * (1.0 - ratio);
		panZ += (mouseY - centerY) * (1.0 - ratio);
	}

	public void render(GuiGraphics graphics, int x, int y, int width, int height, List<RtpSample> samples) {
		mapX = x;
		mapY = y;
		mapWidth = width;
		mapHeight = height;
		int background = 0xEE07111D;
		int gridColor = 0x55304C65;
		int axisColor = 0xFFD4E7F7;
		int ringColor = 0x66517087;

		graphics.fill(x, y, x + width, y + height, background);

		double maxDistance = 300_000.0;
		for (RtpSample sample : samples) {
			maxDistance = Math.max(maxDistance, Math.max(Math.abs(sample.x()), Math.abs(sample.z())));
		}
		maxDistance *= 1.04;

		double drawableRadius = Math.max(1.0, Math.min(width, height) / 2.0 - 12.0);
		double scale = drawableRadius / maxDistance * zoom;
		int centerX = x + width / 2 + (int) panX;
		int centerY = y + height / 2 + (int) panZ;

		graphics.enableScissor(x, y, x + width, y + height);
		try {
			drawGrid(graphics, x, y, width, height, gridColor);
			drawRings(graphics, centerX, centerY, scale, ringColor, x, width);
			drawAxis(graphics, x, y, width, height, centerX, centerY, axisColor);

			graphics.fill(centerX - 2, centerY - 2, centerX + 3, centerY + 3, 0xFFFFD54F);
			graphics.drawString(Minecraft.getInstance().font, "0,0", centerX + 5, centerY + 4,
					0xFFFFD54F, false);
			graphics.drawString(Minecraft.getInstance().font, "X", x + width - 9, centerY - 10,
					0xFFD4E7F7, false);
			graphics.drawString(Minecraft.getInstance().font, "Z", centerX + 4, y + 4,
					0xFFD4E7F7, false);

			for (RtpSample sample : samples) {
				int sampleX = centerX + (int) (sample.x() * scale);
				int sampleY = centerY + (int) (sample.z() * scale);
				if (sampleX < x || sampleX >= x + width || sampleY < y || sampleY >= y + height) {
					continue;
				}
				int color = colorForRegion(sample.requestedRegion());
				graphics.fill(sampleX - 2, sampleY - 2, sampleX + 3, sampleY + 3, color);
			}
		} finally {
			graphics.disableScissor();
		}
		graphics.renderOutline(x, y, width, height, 0xFF355068);
	}

	private void drawGrid(GuiGraphics graphics, int x, int y, int width, int height, int color) {
		for (int offset = 0; offset <= width; offset += 80) {
			graphics.fill(x + offset, y, x + offset + 1, y + height, color);
		}
		for (int offset = 0; offset <= height; offset += 80) {
			graphics.fill(x, y + offset, x + width, y + offset + 1, color);
		}
	}

	private void drawAxis(GuiGraphics graphics, int x, int y, int width, int height,
			int centerX, int centerY, int color) {
		if (centerY >= y && centerY <= y + height) {
			graphics.fill(x, centerY, x + width, centerY + 1, color);
		}
		if (centerX >= x && centerX <= x + width) {
			graphics.fill(centerX, y, centerX + 1, y + height, color);
		}
	}

	private void drawRings(GuiGraphics graphics, int centerX, int centerY, double scale, int color,
			int mapX, int mapWidth) {
		for (int distance : RING_DISTANCES) {
			int radius = (int) (distance * scale);
			drawCircle(graphics, centerX, centerY, radius, color);
			int labelX = centerX + radius + 3;
			if (labelX >= mapX && labelX < mapX + mapWidth - 24) {
				graphics.drawString(Minecraft.getInstance().font, distance / 1_000 + "k",
						labelX, centerY + 2, 0xFF78909C, false);
			}
		}
	}

	private void drawCircle(GuiGraphics graphics, int centerX, int centerY, int radius, int color) {
		if (radius <= 0) {
			return;
		}
		for (int angle = 0; angle < 360; angle++) {
			double radians = Math.toRadians(angle);
			int px = centerX + (int) (Math.cos(radians) * radius);
			int py = centerY + (int) (Math.sin(radians) * radius);
			graphics.fill(px, py, px + 1, py + 1, color);
		}
	}

	public static int colorForRegion(String requestedRegion) {
		RtpRegion region = RtpRegion.fromIdOrNull(requestedRegion);
		if (region == null) {
			return 0xFF90A4AE;
		}
		return switch (region) {
			case EAST -> 0xFF42A5F5;
			case WEST -> 0xFF26C6DA;
			case EU_CENTRAL -> 0xFF66BB6A;
			case EU_WEST -> 0xFFD4E157;
			case ASIA -> 0xFFFFA726;
			case OCEANIA -> 0xFFAB47BC;
		};
	}
}
