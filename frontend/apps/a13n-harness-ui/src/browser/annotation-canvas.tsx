import { useEffect, useMemo, useRef } from "react";
import type Konva from "konva";
import {
  Arrow,
  Ellipse,
  Group,
  Image,
  Layer,
  Line,
  Rect,
  Shape,
  Stage,
  Text,
  Transformer,
} from "react-konva";
import { resizeAnnotation } from "./annotation-model";
import type {
  Annotation,
  Bounds,
  Document,
  Point,
  Tool,
} from "./annotation-model";

export function AnnotationCanvas({
  source,
  document,
  draft,
  cropDraft,
  selected,
  tool,
  disabled,
  editingText,
  scale,
  contentRef,
  onSelect,
  onChange,
  onText,
  onStart,
  onMove,
  onEnd,
  onCancel,
}: {
  source: HTMLCanvasElement;
  document: Document;
  draft: Annotation | null;
  cropDraft: Bounds | null;
  selected: string | null;
  tool: Tool;
  disabled: boolean;
  editingText: string | null;
  scale: number;
  contentRef: React.RefObject<Konva.Group | null>;
  onSelect: (id: string | null) => void;
  onChange: (mark: Annotation) => void;
  onText: (mark: Annotation) => void;
  onStart: (point: Point) => void;
  onMove: (point: Point, constrained: boolean) => void;
  onEnd: () => void;
  onCancel: () => void;
}) {
  const stage = useRef<Konva.Stage>(null);
  const transformer = useRef<Konva.Transformer>(null);
  const pixelated = useMemo(() => {
    const canvas = window.document.createElement("canvas");
    canvas.width = Math.max(1, Math.ceil(source.width / 14));
    canvas.height = Math.max(1, Math.ceil(source.height / 14));
    canvas
      .getContext("2d")!
      .drawImage(source, 0, 0, canvas.width, canvas.height);
    return canvas;
  }, [source]);
  useEffect(() => {
    const node = contentRef.current?.findOne(
      (item: Konva.Node) => item.id() === selected,
    );
    transformer.current?.nodes(
      node && tool === "select" && !disabled ? [node] : [],
    );
  }, [contentRef, selected, document, tool, scale, disabled]);
  const crop = document.crop;
  const point = () => {
    const position = stage.current?.getPointerPosition();
    return position
      ? { x: position.x / scale + crop.x, y: position.y / scale + crop.y }
      : null;
  };
  const marks = draft ? [...document.annotations, draft] : document.annotations;
  const renderMark = (mark: Annotation) => {
    const props = {
      id: mark.id,
      x: mark.x,
      y: mark.y,
      scaleX: mark.scaleX,
      scaleY: mark.scaleY,
      stroke: mark.color,
      strokeWidth: mark.strokeWidth,
      visible: mark.id !== editingText,
      draggable: tool === "select" && !disabled,
      listening: tool === "select" && !disabled,
      onPointerDown: (event: Konva.KonvaEventObject<PointerEvent>) => {
        event.cancelBubble = true;
        onSelect(mark.id);
      },
      onDblClick: () => onText(mark),
      onDblTap: () => onText(mark),
      onDragEnd: (event: Konva.KonvaEventObject<DragEvent>) =>
        onChange({ ...mark, x: event.target.x(), y: event.target.y() }),
      onTransformEnd: (event: Konva.KonvaEventObject<Event>) => {
        const resized = resizeAnnotation(mark, {
          x: event.target.x(),
          y: event.target.y(),
          scaleX: event.target.scaleX(),
          scaleY: event.target.scaleY(),
        });
        // react-konva only reapplies changed props; reset the imperative transform too.
        event.target.scale({ x: resized.scaleX, y: resized.scaleY });
        onChange(resized);
      },
    };
    switch (mark.kind) {
      case "arrow":
        return (
          <Arrow
            key={mark.id}
            {...props}
            points={mark.points}
            fill={mark.color}
            pointerLength={mark.strokeWidth * 4}
            pointerWidth={mark.strokeWidth * 4}
            hitStrokeWidth={16}
          />
        );
      case "pen":
        return (
          <Line
            key={mark.id}
            {...props}
            points={mark.points}
            lineCap="round"
            lineJoin="round"
            hitStrokeWidth={16}
          />
        );
      case "rectangle":
        return (
          <Rect
            key={mark.id}
            {...props}
            width={mark.width}
            height={mark.height}
            hitStrokeWidth={16}
          />
        );
      case "ellipse":
        return (
          <Ellipse
            key={mark.id}
            {...props}
            offsetX={-mark.width / 2}
            offsetY={-mark.height / 2}
            radiusX={mark.width / 2}
            radiusY={mark.height / 2}
            hitStrokeWidth={16}
          />
        );
      case "text":
        return (
          <Text
            key={mark.id}
            {...props}
            text={mark.text}
            fontSize={mark.fontSize}
            fontFamily="sans-serif"
            fill={mark.color}
            strokeEnabled={false}
          />
        );
      case "redact":
        return (
          <Rect
            key={mark.id}
            {...props}
            width={mark.width}
            height={mark.height}
            fill="#000000"
            strokeEnabled={false}
          />
        );
      case "mosaic":
        return (
          <Shape
            key={mark.id}
            {...props}
            width={mark.width}
            height={mark.height}
            strokeEnabled={false}
            fill="black"
            sceneFunc={(context, shape) => {
              // Sample the current source region, including while moving/resizing.
              const x = shape.x(),
                y = shape.y();
              const width = shape.width(),
                height = shape.height();
              context.save();
              context.beginPath();
              context.rect(0, 0, width, height);
              context.clip();
              context.setAttr("imageSmoothingEnabled", false);
              context.drawImage(
                pixelated,
                -x / shape.scaleX(),
                -y / shape.scaleY(),
                source.width / shape.scaleX(),
                source.height / shape.scaleY(),
              );
              context.restore();
            }}
            hitFunc={(context, shape) => {
              context.beginPath();
              context.rect(0, 0, shape.width(), shape.height());
              context.fillStrokeShape(shape);
            }}
          />
        );
    }
  };
  return (
    <Stage
      ref={stage}
      width={crop.width * scale}
      height={crop.height * scale}
      onPointerDown={(event) => {
        if (
          disabled ||
          !event.evt.isPrimary ||
          event.evt.button !== 0 ||
          event.target.getParent()?.getClassName() === "Transformer"
        )
          return;
        const position = point();
        if (!position) return;
        if (tool === "select") onSelect(null);
        else {
          event.evt.target instanceof Element &&
            event.evt.target.setPointerCapture(event.evt.pointerId);
          onStart(position);
        }
      }}
      onPointerMove={(event) => {
        const position = point();
        if (position) onMove(position, event.evt.shiftKey);
      }}
      onPointerUp={onEnd}
      onPointerCancel={onCancel}
    >
      <Layer
        scaleX={scale}
        scaleY={scale}
        x={-crop.x * scale}
        y={-crop.y * scale}
      >
        <Group ref={contentRef}>
          <Image image={source} listening={false} />
          {marks.filter((mark) => mark.kind !== "redact").map(renderMark)}
          {/* Opaque redactions remain above source-sampling mosaic regions. */}
          {marks.filter((mark) => mark.kind === "redact").map(renderMark)}
        </Group>
        <Transformer
          ref={transformer}
          rotateEnabled={false}
          flipEnabled={false}
          keepRatio={false}
          ignoreStroke
          anchorSize={9}
          borderStroke="#3b82f6"
          anchorStroke="#3b82f6"
          anchorCornerRadius={2}
          boundBoxFunc={(oldBox, newBox) =>
            newBox.width < 4 || newBox.height < 4 ? oldBox : newBox
          }
        />
        {cropDraft && (
          <Group listening={false}>
            <Rect
              x={crop.x}
              y={crop.y}
              width={crop.width}
              height={cropDraft.y - crop.y}
              fill="rgba(0,0,0,0.4)"
            />
            <Rect
              x={crop.x}
              y={cropDraft.y + cropDraft.height}
              width={crop.width}
              height={crop.y + crop.height - cropDraft.y - cropDraft.height}
              fill="rgba(0,0,0,0.4)"
            />
            <Rect
              x={crop.x}
              y={cropDraft.y}
              width={cropDraft.x - crop.x}
              height={cropDraft.height}
              fill="rgba(0,0,0,0.4)"
            />
            <Rect
              x={cropDraft.x + cropDraft.width}
              y={cropDraft.y}
              width={crop.x + crop.width - cropDraft.x - cropDraft.width}
              height={cropDraft.height}
              fill="rgba(0,0,0,0.4)"
            />
            <Rect
              {...cropDraft}
              stroke="white"
              strokeWidth={1 / scale}
              dash={[6 / scale, 4 / scale]}
            />
          </Group>
        )}
      </Layer>
    </Stage>
  );
}
