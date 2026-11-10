import styles from "./conversation.module.css";

export function ReaderFades() {
  return (
    <>
      <div className={styles.readerFade} data-edge="top" aria-hidden="true" />
      <div
        className={styles.readerFade}
        data-edge="bottom"
        aria-hidden="true"
      />
    </>
  );
}
