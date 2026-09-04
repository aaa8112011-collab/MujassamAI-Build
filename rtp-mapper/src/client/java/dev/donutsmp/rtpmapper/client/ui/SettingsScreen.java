package dev.donutsmp.rtpmapper.client.ui;

import dev.donutsmp.rtpmapper.config.ConfigManager;
import dev.donutsmp.rtpmapper.config.MapperConfig;
import dev.donutsmp.rtpmapper.engine.RtpRegion;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

import java.util.ArrayList;
import java.util.List;

public final class SettingsScreen extends Screen {
	private final Screen parent;
	private final MapperConfig draft;
	private EditBox thresholdBox;
	private EditBox timeoutBox;
	private EditBox stabilizeTicksBox;
	private EditBox serverHostBox;
	private EditBox regionsBox;

	public SettingsScreen(Screen parent) {
		super(Component.literal("RTP Mapper Settings"));
		this.parent = parent;
		this.draft = ConfigManager.get().getConfig().copy();
	}

	@Override
	protected void init() {
		int x = width / 2 - 150;
		int y = 42;
		int fieldWidth = 300;
		int fieldHeight = 20;

		thresholdBox = addField(x, y, fieldWidth, fieldHeight,
				"Teleport distance threshold", String.valueOf(draft.teleportConfirmBlocks));
		y += 34;
		timeoutBox = addField(x, y, fieldWidth, fieldHeight,
				"Detection timeout (seconds)", String.valueOf(draft.teleportConfirmTimeoutSeconds));
		y += 34;
		stabilizeTicksBox = addField(x, y, fieldWidth, fieldHeight,
				"Landing stabilization (ticks)", String.valueOf(draft.landingStabilizeTicks));
		y += 34;
		serverHostBox = addField(x, y, fieldWidth, fieldHeight,
				"Allowed DonutSMP host", draft.serverHost);
		y += 34;
		regionsBox = addField(x, y, fieldWidth, fieldHeight,
				"Suggested regions (comma-separated)", String.join(", ", draft.enabledRegions));
		y += 30;

		addRenderableWidget(CycleButton.onOffBuilder(draft.autoSaveAfterSample)
				.create(x, y, fieldWidth, 20, Component.literal("Auto-save samples"),
						(button, value) -> draft.autoSaveAfterSample = value));
		y += 24;
		addRenderableWidget(CycleButton.onOffBuilder(draft.hudEnabled)
				.create(x, y, fieldWidth, 20, Component.literal("HUD enabled"),
						(button, value) -> draft.hudEnabled = value));
		y += 24;
		addRenderableWidget(CycleButton.onOffBuilder(draft.hudShowMiniMap)
				.create(x, y, fieldWidth, 20, Component.literal("HUD mini-map"),
						(button, value) -> draft.hudShowMiniMap = value));
		y += 24;

		HudCorner selectedCorner = HudCorner.fromId(draft.hudCorner);
		addRenderableWidget(CycleButton.builder(HudCorner::label, selectedCorner)
				.withValues(HudCorner.values())
				.create(x, y, fieldWidth, 20, Component.literal("HUD corner"),
						(button, value) -> draft.hudCorner = value.id));
		y += 30;

		addRenderableWidget(Button.builder(Component.literal("Save"), button -> saveAndClose())
				.bounds(x, y, 145, 20).build());
		addRenderableWidget(Button.builder(Component.literal("Cancel"), button -> onClose())
				.bounds(x + 155, y, 145, 20).build());
	}

	private EditBox addField(int x, int y, int width, int height, String narration, String value) {
		EditBox field = new EditBox(font, x, y, width, height, Component.literal(narration));
		field.setValue(value);
		addRenderableWidget(field);
		return field;
	}

	@Override
	public void render(GuiGraphics graphics, int mouseX, int mouseY, float partialTick) {
		renderBackground(graphics, mouseX, mouseY, partialTick);
		graphics.drawCenteredString(font, title, width / 2, 10, 0xFFFFFFFF);
		int x = width / 2 - 150;
		graphics.drawString(font, "Teleport distance threshold", x, 30, 0xFFB0BEC5, false);
		graphics.drawString(font, "Detection timeout (seconds)", x, 64, 0xFFB0BEC5, false);
		graphics.drawString(font, "Landing stabilization (ticks)", x, 98, 0xFFB0BEC5, false);
		graphics.drawString(font, "Allowed DonutSMP host", x, 132, 0xFFB0BEC5, false);
		graphics.drawString(font, "Suggested regions (east, west, eu central, eu west, asia, oceania)",
				x, 166, 0xFFB0BEC5, false);
		graphics.drawCenteredString(font,
				"Passive only: this mod never sends commands or controls movement.",
				width / 2, height - 14, 0xFF81C784);
		super.render(graphics, mouseX, mouseY, partialTick);
	}

	private void saveAndClose() {
		try {
			draft.teleportConfirmBlocks = Integer.parseInt(thresholdBox.getValue().trim());
			draft.teleportConfirmTimeoutSeconds = Integer.parseInt(timeoutBox.getValue().trim());
			draft.landingStabilizeTicks = Integer.parseInt(stabilizeTicksBox.getValue().trim());
		} catch (NumberFormatException exception) {
			return;
		}

		draft.serverHost = serverHostBox.getValue().trim();
		draft.enabledRegions = parseRegions(regionsBox.getValue());
		draft.normalize();
		ConfigManager.get().update(draft);
		onClose();
	}

	private List<String> parseRegions(String raw) {
		List<String> regions = new ArrayList<>();
		for (String part : raw.split(",")) {
			RtpRegion region = RtpRegion.fromIdOrNull(part);
			if (region != null && !regions.contains(region.id())) {
				regions.add(region.id());
			}
		}
		if (regions.isEmpty()) {
			regions.addAll(RtpRegion.canonicalIds());
		}
		return regions;
	}

	@Override
	public void onClose() {
		if (minecraft != null) {
			minecraft.setScreen(parent);
		}
	}

	private enum HudCorner {
		TOP_LEFT("top_left", "Top Left"),
		TOP_RIGHT("top_right", "Top Right"),
		BOTTOM_LEFT("bottom_left", "Bottom Left"),
		BOTTOM_RIGHT("bottom_right", "Bottom Right");

		private final String id;
		private final String label;

		HudCorner(String id, String label) {
			this.id = id;
			this.label = label;
		}

		static HudCorner fromId(String id) {
			for (HudCorner corner : values()) {
				if (corner.id.equals(id)) {
					return corner;
				}
			}
			return TOP_LEFT;
		}

		static Component label(HudCorner corner) {
			return Component.literal(corner.label);
		}
	}
}
