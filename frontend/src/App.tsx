import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './core/auth/AuthContext';
import { ProtectedRoute } from './core/auth/ProtectedRoute';
import { LoginPage } from './login/LoginPage';
import { SetupPage } from './setup/SetupPage';
import { CrmLayout } from './crm/CrmLayout';
import { CrmDashboardPage } from './crm/CrmDashboardPage';
import { ContactsPage } from './crm/ContactsPage';
import { ContactDetailPage } from './crm/ContactDetailPage';
import { CompaniesPage } from './crm/CompaniesPage';
import { CompanyDetailPage } from './crm/CompanyDetailPage';
import { PipelinePage } from './crm/PipelinePage';
import { TasksPage } from './crm/TasksPage';
import { ToastViewport } from './shared/ToastViewport';
import { ConfirmHost } from './shared/ConfirmHost';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/setup"
            element={
              <ProtectedRoute>
                <SetupPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/crm"
            element={
              <ProtectedRoute>
                <CrmLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<CrmDashboardPage />} />
            <Route path="contacts" element={<ContactsPage />} />
            <Route path="contacts/:id" element={<ContactDetailPage />} />
            <Route path="companies" element={<CompaniesPage />} />
            <Route path="companies/:id" element={<CompanyDetailPage />} />
            <Route path="pipeline" element={<PipelinePage />} />
            <Route path="tasks" element={<TasksPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/crm" replace />} />
        </Routes>
        <ConfirmHost />
        <ToastViewport />
      </BrowserRouter>
    </AuthProvider>
  );
}
