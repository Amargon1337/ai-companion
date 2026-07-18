=== POTENTIAL DEAD CLASSES ===
Class UserState defined in ['policy_layer.py'] might be unused.
Class ResponseMode defined in ['policy_layer.py'] might be unused.
Class Prediction defined in ['reasoning.py'] might be unused.
Class UserMood defined in ['llm\\client.py'] might be unused.
Class MessageAnalysis defined in ['llm\\client.py'] might be unused.
Class FactItem defined in ['llm\\client.py'] might be unused.
Class FactExtractionResult defined in ['llm\\client.py'] might be unused.
Class ConsolidationItem defined in ['llm\\client.py'] might be unused.
Class ConsolidationResult defined in ['llm\\client.py'] might be unused.
Class CausalLinkItem defined in ['llm\\client.py'] might be unused.
Class CausalLinkExtractionResult defined in ['llm\\client.py'] might be unused.
Class ReflectionItem defined in ['llm\\client.py'] might be unused.
Class ReflectionResult defined in ['llm\\client.py'] might be unused.
Class PatternItem defined in ['llm\\client.py'] might be unused.
Class PatternExtractionResult defined in ['llm\\client.py'] might be unused.
Class CommPrefItem defined in ['llm\\client.py'] might be unused.
Class CommPrefExtractionResult defined in ['llm\\client.py'] might be unused.
Class HumanModelItem defined in ['llm\\client.py'] might be unused.
Class HumanModelExtractionResult defined in ['llm\\client.py'] might be unused.
Class LifeTransitionItem defined in ['llm\\client.py'] might be unused.
Class LifeTransitionExtractionResult defined in ['llm\\client.py'] might be unused.
Class PersonalityPipelineResult defined in ['llm\\client.py'] might be unused.
Class KnowledgeDomainItem defined in ['llm\\client.py'] might be unused.
Class KnowledgeDomainsExtractionResult defined in ['llm\\client.py'] might be unused.
Class PingReason defined in ['proactive\\reasons.py'] might be unused.

=== POTENTIAL DEAD FUNCTIONS ===
Function _sync_micro_update defined in ['background_scheduler.py'] might be unused.
Function reset_context defined in ['bot_core.py'] might be unused.
Function show_goals defined in ['bot_core.py'] might be unused.
Function show_reasoning_state defined in ['bot_core.py'] might be unused.
Function show_todos defined in ['bot_core.py'] might be unused.
Function clear_done_todos defined in ['bot_core.py', 'storage\\sqlite_db.py'] might be unused.
Function show_self_description defined in ['bot_core.py'] might be unused.
Function show_selfmap defined in ['bot_core.py'] might be unused.
Function _load_sync defined in ['bot_core.py'] might be unused.
Function _signal_handler defined in ['main.py'] might be unused.
Function from_analyzer_state defined in ['policy_layer.py'] might be unused.
Function decide_policy defined in ['policy_layer.py'] might be unused.
Function get_prediction_context defined in ['reasoning.py'] might be unused.
Function auto_reasoning_context defined in ['reasoning.py'] might be unused.
Function add_prediction defined in ['reasoning.py'] might be unused.
Function build_situation_model defined in ['reasoning.py'] might be unused.
Function analyze_causality defined in ['reasoning.py'] might be unused.
Function get_predictions_summary defined in ['reasoning.py'] might be unused.
Function get_confidence defined in ['self_model.py'] might be unused.
Function _sync_io defined in ['user_model.py'] might be unused.
Function cmd_start defined in ['handlers\\chat.py'] might be unused.
Function cmd_help defined in ['handlers\\chat.py'] might be unused.
Function cmd_summary defined in ['handlers\\chat.py'] might be unused.
Function cmd_personality defined in ['handlers\\chat.py'] might be unused.
Function cmd_remember defined in ['handlers\\chat.py'] might be unused.
Function cmd_continuity defined in ['handlers\\chat.py'] might be unused.
Function cmd_timeline defined in ['handlers\\chat.py'] might be unused.
Function cmd_metrics defined in ['handlers\\chat.py'] might be unused.
Function cmd_search defined in ['handlers\\chat.py'] might be unused.
Function inline_actions defined in ['handlers\\chat.py'] might be unused.
Function command_confirm defined in ['handlers\\chat.py'] might be unused.
Function multimodal_handler defined in ['handlers\\chat.py'] might be unused.
Function text_handler defined in ['handlers\\chat.py'] might be unused.
Function tiktok_handler defined in ['handlers\\chat.py'] might be unused.
Function parse_year_from_text defined in ['handlers\\commands.py'] might be unused.
Function show_facts defined in ['handlers\\commands.py'] might be unused.
Function show_notes defined in ['handlers\\commands.py'] might be unused.
Function export_diary defined in ['handlers\\commands.py'] might be unused.
Function auto_add_event_from_message defined in ['handlers\\commands.py'] might be unused.
Function show_selfie defined in ['handlers\\commands.py'] might be unused.
Function show_week_digest defined in ['handlers\\commands.py'] might be unused.
Function show_retrospective defined in ['handlers\\commands.py'] might be unused.
Function show_context defined in ['handlers\\commands.py'] might be unused.
Function voice_handler defined in ['handlers\\media.py'] might be unused.
Function document_handler defined in ['handlers\\media.py'] might be unused.
Function media_handler defined in ['handlers\\media.py'] might be unused.
Function video_handler defined in ['handlers\\media.py'] might be unused.
Function recognize defined in ['handlers\\media.py'] might be unused.
Function format_grounding_sources defined in ['llm\\client.py'] might be unused.
Function upload_file defined in ['llm\\client.py'] might be unused.
Function delete_file defined in ['llm\\client.py'] might be unused.
Function aio_get_file defined in ['llm\\client.py'] might be unused.
Function async_oneshot defined in ['llm\\client.py'] might be unused.
Function async_upload_file defined in ['llm\\client.py'] might be unused.
Function async_delete_file defined in ['llm\\client.py'] might be unused.
Function update_master_summary defined in ['llm\\master_summary.py'] might be unused.
Function generate_reflections defined in ['llm\\pipeline.py'] might be unused.
Function _personality_critical_section defined in ['llm\\pipeline.py'] might be unused.
Function _sync_stages defined in ['llm\\pipeline.py'] might be unused.
Function create_default_session defined in ['llm\\sessions.py'] might be unused.
Function decorator defined in ['llm\\telemetry.py'] might be unused.
Function wrapper defined in ['llm\\telemetry.py'] might be unused.
Function async_wrapper defined in ['llm\\telemetry.py'] might be unused.
Function score_message_importance defined in ['memory\\importance.py'] might be unused.
Function retrieval_score defined in ['memory\\importance.py'] might be unused.
Function lock defined in ['memory\\store.py'] might be unused.
Function _assert_locked defined in ['memory\\store.py'] might be unused.
Function build_personality_snapshot_text defined in ['memory\\store.py'] might be unused.
Function log_message defined in ['memory\\store.py'] might be unused.
Function update_pattern defined in ['memory\\store.py'] might be unused.
Function update_transition defined in ['memory\\store.py'] might be unused.
Function get_pending_transitions defined in ['memory\\store.py'] might be unused.
Function touch_transition defined in ['memory\\store.py'] might be unused.
Function save_summary defined in ['memory\\store.py'] might be unused.
Function apply_importance_decay defined in ['memory\\store.py'] might be unused.
Function stats defined in ['memory\\store.py'] might be unused.
Function analyze_retrieval_effectiveness defined in ['memory\\store.py'] might be unused.
Function async_add_fact defined in ['memory\\store.py'] might be unused.
Function async_update_fact defined in ['memory\\store.py'] might be unused.
Function async_search_facts defined in ['memory\\store.py'] might be unused.
Function async_add_relation defined in ['memory\\store.py'] might be unused.
Function async_add_pattern defined in ['memory\\store.py'] might be unused.
Function async_search_patterns defined in ['memory\\store.py'] might be unused.
Function async_search_reflections defined in ['memory\\store.py'] might be unused.
Function async_add_reflection defined in ['memory\\store.py'] might be unused.
Function cosine_similarity defined in ['memory\\vector_index.py', 'memory\\vector_index.py'] might be unused.
Function collect_goal_context defined in ['proactive\\collector.py'] might be unused.
Function collect_conversation_context defined in ['proactive\\collector.py'] might be unused.
Function collect_emotional_context defined in ['proactive\\collector.py'] might be unused.
Function collect_achievement_context defined in ['proactive\\collector.py'] might be unused.
Function collect_silence_context defined in ['proactive\\collector.py'] might be unused.
Function collect_memory_callback_context defined in ['proactive\\collector.py'] might be unused.
Function record_ping_sent defined in ['proactive\\engagement.py', 'proactive\\telemetry.py'] might be unused.
Function priority defined in ['proactive\\reasons.py'] might be unused.
Function record_ping_reply defined in ['proactive\\telemetry.py'] might be unused.
Function get_proactive_stats defined in ['proactive\\telemetry.py'] might be unused.
Function read_jsonl defined in ['storage\\jsonl.py'] might be unused.
Function batch_insert_facts defined in ['storage\\sqlite_db.py'] might be unused.
Function batch_insert_relations defined in ['storage\\sqlite_db.py'] might be unused.
Function batch_insert_messages defined in ['storage\\sqlite_db.py'] might be unused.
Function batch_insert_reflections defined in ['storage\\sqlite_db.py'] might be unused.
Function batch_insert_beliefs defined in ['storage\\sqlite_db.py'] might be unused.
Function async_insert_belief defined in ['storage\\sqlite_db.py'] might be unused.
Function async_upsert_goal defined in ['storage\\sqlite_db.py'] might be unused.
Function async_list_goals defined in ['storage\\sqlite_db.py'] might be unused.
Function async_update_goal defined in ['storage\\sqlite_db.py'] might be unused.
Function delete_goal defined in ['storage\\sqlite_db.py'] might be unused.
Function async_delete_goal defined in ['storage\\sqlite_db.py'] might be unused.
Function async_upsert_causal_link defined in ['storage\\sqlite_db.py'] might be unused.
Function async_list_causal_links defined in ['storage\\sqlite_db.py'] might be unused.
Function delete_causal_link defined in ['storage\\sqlite_db.py'] might be unused.
Function async_delete_causal_link defined in ['storage\\sqlite_db.py'] might be unused.
Function async_list_predictions defined in ['storage\\sqlite_db.py'] might be unused.
Function delete_prediction defined in ['storage\\sqlite_db.py'] might be unused.
Function async_delete_prediction defined in ['storage\\sqlite_db.py'] might be unused.
Function update_pattern_status defined in ['storage\\sqlite_db.py'] might be unused.
Function delete_life_transition defined in ['storage\\sqlite_db.py'] might be unused.
Function save_session defined in ['storage\\sqlite_db.py'] might be unused.
Function load_sessions defined in ['storage\\sqlite_db.py'] might be unused.
Function insert_retrieval_metrics defined in ['storage\\sqlite_db.py'] might be unused.
Function increment_fact_usage defined in ['storage\\sqlite_db.py'] might be unused.
Function create_temporal_counter defined in ['storage\\sqlite_db.py'] might be unused.
Function update_temporal_counter defined in ['storage\\sqlite_db.py'] might be unused.
Function delete_temporal_counter defined in ['storage\\sqlite_db.py'] might be unused.
Function pause_temporal_counter defined in ['storage\\sqlite_db.py'] might be unused.
Function resume_temporal_counter defined in ['storage\\sqlite_db.py'] might be unused.
Function list_temporal_counters defined in ['storage\\sqlite_db.py'] might be unused.
Function list_temporal_counter_pauses defined in ['storage\\sqlite_db.py'] might be unused.
Function async_list_permanent_notes defined in ['storage\\sqlite_db.py'] might be unused.
Function save_todo defined in ['storage\\sqlite_db.py'] might be unused.
Function list_todos defined in ['storage\\sqlite_db.py'] might be unused.
Function save_monthbook defined in ['storage\\sqlite_db.py'] might be unused.
Function load_monthbook defined in ['storage\\sqlite_db.py'] might be unused.
Function upsert_prospective_task defined in ['storage\\sqlite_db.py'] might be unused.
Function due_prospective_tasks defined in ['storage\\sqlite_db.py'] might be unused.
Function mark_prospective_task_triggered defined in ['storage\\sqlite_db.py'] might be unused.
Function async_batch_insert_facts defined in ['storage\\sqlite_db.py'] might be unused.
Function async_batch_insert_relations defined in ['storage\\sqlite_db.py'] might be unused.
Function async_batch_insert_messages defined in ['storage\\sqlite_db.py'] might be unused.
Function async_batch_insert_reflections defined in ['storage\\sqlite_db.py'] might be unused.
Function async_batch_insert_beliefs defined in ['storage\\sqlite_db.py'] might be unused.

=== TODOs & FIXMEs ===

=== NOT IMPLEMENTED / PASS ===
background_scheduler.py:48 -> pass
bot_core.py:96 -> pass
bot_core.py:946 -> pass
bot_core.py:995 -> pass
main.py:191 -> pass
main.py:235 -> except NotImplementedError:
main.py:236 -> pass
main.py:250 -> pass
main.py:261 -> pass
reasoning.py:167 -> pass
self_model.py:29 -> pass
self_model.py:121 -> pass
user_model.py:431 -> pass
handlers\chat.py:214 -> pass
handlers\media.py:126 -> pass
llm\client.py:145 -> pass
memory\identity_vault.py:74 -> pass
memory\store.py:83 -> pass
memory\store.py:161 -> pass
memory\vector_index.py:191 -> pass
storage\jsonl.py:64 -> pass
storage\jsonl.py:72 -> pass
storage\jsonl.py:77 -> pass
storage\sqlite_db.py:398 -> pass
storage\sqlite_db.py:432 -> pass
storage\sqlite_db.py:436 -> pass
