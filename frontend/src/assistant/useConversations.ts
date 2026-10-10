// CakeCRM — assistant conversation list (list / open / delete / rename).

import { useCallback, useState } from 'react';

import { api } from '../core/api/client';
import { toast } from '../shared/toast';
import type { Conversation, RunningTurn, ServerMessage } from './types';

type OpenedConversation = { id: string; messages: ServerMessage[]; running_turn?: RunningTurn | null };

const API = '/api/assistant';

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<{ conversations: Conversation[] }>(`${API}/conversations`);
      setConversations(data.conversations);
    } catch {
      toast.error('Could not load conversations.');
    } finally {
      setLoading(false);
    }
  }, []);

  const openConversation = useCallback(
    async (id: string): Promise<OpenedConversation | null> => {
      try {
        // `running_turn` (#282) is absent on an older backend.
        return await api<OpenedConversation>(`${API}/conversations/${id}`);
      } catch {
        toast.error('Could not open that conversation.');
        return null;
      }
    },
    [],
  );

  const remove = useCallback(async (id: string) => {
    try {
      await api(`${API}/conversations/${id}`, { method: 'DELETE' });
      setConversations((cs) => cs.filter((c) => c.id !== id));
    } catch {
      toast.error('Could not delete that conversation.');
    }
  }, []);

  const rename = useCallback(async (id: string, title: string) => {
    try {
      const data = await api<{ title: string }>(`${API}/conversations/${id}/title`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      });
      setConversations((cs) => cs.map((c) => (c.id === id ? { ...c, title: data.title } : c)));
    } catch {
      toast.error('Could not rename that conversation.');
    }
  }, []);

  return { conversations, loading, load, openConversation, remove, rename };
}
