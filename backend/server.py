@@
-    await db.connection_events.create_index([("createdAt", 1)], expireAfterSeconds=2592000, name="conn_events_ttl_30d")
+    await db.connection_events.create_index([("createdAt", 1)], expireAfterSeconds=2592000, name="conn_events_ttl_30d")
@@
-            logger.warning(f"God Candle backfill warning: {e}")
+            logger.warning(f"God Candle backfill warning: {e}")
@@
-    logger.info("Rugs Socket Service started")
+    logger.info("Rugs Socket Service started")