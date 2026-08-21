// Todo-GTD — the shared project close-out action. ONE definition used by both the
// project card grid and the project detail header, so the Complete/Reactivate flow
// cannot drift between the two.
import { toast } from '../../shared/toast';
import { updateProject } from './api';
import type { TodoProjectStatus } from './types';

/** PUT a project status change; toasts on failure. Returns success. */
export async function updateProjectStatus(
  projectId: number, status: TodoProjectStatus,
): Promise<boolean> {
  try {
    await updateProject(projectId, { status });
    return true;
  } catch {
    toast.error('Failed to update project.');
    return false;
  }
}
