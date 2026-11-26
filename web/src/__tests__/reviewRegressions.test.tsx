import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountsView } from "../components/AccountsView";
import { BacktestLab } from "../components/BacktestLab";
import { NewsView } from "../components/NewsView";

afterEach(() => {
  cleanup();
});

describe("Code Review 回归测试", () => {
  it("BacktestLab 默认参数满足 HTML5 校验并可提交", () => {
    const onRunBacktest = vi.fn();
    const { container } = render(
      <BacktestLab busyAction={null} result={null} onRunBacktest={onRunBacktest} />
    );

    const form = container.querySelector("form");
    expect(form).not.toBeNull();
    expect(form?.checkValidity()).toBe(true);

    fireEvent.submit(form!);
    expect(onRunBacktest).toHaveBeenCalledWith({
      initial_cash: 100000,
      trade_grams: 2,
      dca_step_interval: 5,
    });
  });

  it("AccountsView 拒绝非 https 授权链接并在 pending_login 失效时隐藏过期链接", () => {
    const { rerender } = render(
      <AccountsView
        authStatus={{
          logged_in: false,
          usable_seconds: null,
          expires_at: null,
          reauth_recommended: false,
          refresh_supported: false,
          pending_login: true,
          pending_login_expires_at: "2026-11-26T10:00:00Z",
          authorize_url: "javascript:alert(1)",
        }}
        authFailed={false}
        jdHoldings={null}
        jdHoldingsMessage={null}
        holdingsConfirmedAt={null}
        busyAction={null}
        refreshing={false}
        onRefresh={vi.fn()}
        onStartLogin={vi.fn(async () => null)}
        onCompleteLogin={vi.fn(async () => true)}
        onVerifyAuth={vi.fn(async () => {})}
        onLogout={vi.fn(async () => true)}
      />
    );

    expect(screen.queryByText("打开京东授权页")).toBeNull();

    rerender(
      <AccountsView
        authStatus={{
          logged_in: false,
          usable_seconds: null,
          expires_at: null,
          reauth_recommended: false,
          refresh_supported: false,
          pending_login: true,
          pending_login_expires_at: "2026-11-26T10:00:00Z",
          authorize_url: "https://passport.jd.com/auth",
        }}
        authFailed={false}
        jdHoldings={null}
        jdHoldingsMessage={null}
        holdingsConfirmedAt={null}
        busyAction={null}
        refreshing={false}
        onRefresh={vi.fn()}
        onStartLogin={vi.fn(async () => null)}
        onCompleteLogin={vi.fn(async () => true)}
        onVerifyAuth={vi.fn(async () => {})}
        onLogout={vi.fn(async () => true)}
      />
    );
    expect(screen.getByText("打开京东授权页")).toBeTruthy();

    rerender(
      <AccountsView
        authStatus={{
          logged_in: false,
          usable_seconds: null,
          expires_at: null,
          reauth_recommended: false,
          refresh_supported: false,
          pending_login: false,
          pending_login_expires_at: null,
          authorize_url: null,
        }}
        authFailed={false}
        jdHoldings={null}
        jdHoldingsMessage={null}
        holdingsConfirmedAt={null}
        busyAction={null}
        refreshing={false}
        onRefresh={vi.fn()}
        onStartLogin={vi.fn(async () => null)}
        onCompleteLogin={vi.fn(async () => true)}
        onVerifyAuth={vi.fn(async () => {})}
        onLogout={vi.fn(async () => true)}
      />
    );
    expect(screen.queryByText("打开京东授权页")).toBeNull();
  });

  it("NewsView 登记宏观日程时校验非法时间不抛 RangeError", () => {
    const onRegisterMacro = vi.fn(async () => true);
    const { container } = render(
      <NewsView
        newsOverview={null}
        refreshing={false}
        onRefresh={vi.fn()}
        onRegisterMacro={onRegisterMacro}
      />
    );

    const nameInput = container.querySelector('input[type="text"]') as HTMLInputElement;
    const dateInput = container.querySelector('input[type="datetime-local"]') as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "CPI" } });
    // 绕过 datetime-local 浏览器格式限制模拟非法字符串
    Object.defineProperty(dateInput, "value", { writable: true, value: "not-a-date" });
    fireEvent.change(dateInput, { target: { value: "not-a-date" } });

    const form = container.querySelector("form")!;
    fireEvent.submit(form);
    expect(onRegisterMacro).not.toHaveBeenCalled();
  });

  it("AccountsView 发起登录后即使旧 authStatus 已加载仍展示回退授权链接与确认按钮", async () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    try {
      const initialStatus = {
        logged_in: false,
        usable_seconds: null,
        expires_at: null,
        reauth_recommended: false,
        refresh_supported: false,
        pending_login: false,
        pending_login_expires_at: null,
        authorize_url: null,
      };
      const onStartLogin = vi.fn(async () => "https://passport.jd.com/new-auth");
      const { rerender } = render(
        <AccountsView
          authStatus={initialStatus}
          authFailed={false}
          jdHoldings={null}
          jdHoldingsMessage={null}
          holdingsConfirmedAt={null}
          busyAction={null}
          refreshing={false}
          onRefresh={vi.fn()}
          onStartLogin={onStartLogin}
          onCompleteLogin={vi.fn(async () => true)}
          onVerifyAuth={vi.fn(async () => {})}
          onLogout={vi.fn(async () => true)}
        />
      );

      fireEvent.click(screen.getAllByRole("button", { name: "登录京东" })[0]);
      expect(await screen.findByText("打开京东授权页")).toBeTruthy();
      const confirmBtn = screen.getByRole("button", { name: /我已完成授权/ }) as HTMLButtonElement;
      expect(confirmBtn.disabled).toBe(false);

      // 收到新的 authStatus 且 pending_login 明确为 false 后清除回退链接
      rerender(
        <AccountsView
          authStatus={{ ...initialStatus }}
          authFailed={false}
          jdHoldings={null}
          jdHoldingsMessage={null}
          holdingsConfirmedAt={null}
          busyAction={null}
          refreshing={false}
          onRefresh={vi.fn()}
          onStartLogin={onStartLogin}
          onCompleteLogin={vi.fn(async () => true)}
          onVerifyAuth={vi.fn(async () => {})}
          onLogout={vi.fn(async () => true)}
        />
      );
      expect(screen.queryByText("打开京东授权页")).toBeNull();
    } finally {
      openSpy.mockRestore();
    }
  });

  it("NewsView 拦截超过 72 小时的锁定窗口并将空输入回退默认值 2", () => {
    const onRegisterMacro = vi.fn(async () => true);
    const { container } = render(
      <NewsView
        newsOverview={null}
        refreshing={false}
        onRefresh={vi.fn()}
        onRegisterMacro={onRegisterMacro}
      />
    );

    const nameInput = container.querySelector('input[type="text"]') as HTMLInputElement;
    const dateInput = container.querySelector('input[type="datetime-local"]') as HTMLInputElement;
    const numberInputs = container.querySelectorAll('input[type="number"]');
    const beforeInput = numberInputs[0] as HTMLInputElement;
    const afterInput = numberInputs[1] as HTMLInputElement;
    const form = container.querySelector("form")!;

    fireEvent.change(nameInput, { target: { value: "非农就业" } });
    fireEvent.change(dateInput, { target: { value: "2026-12-01T20:30" } });
    fireEvent.change(beforeInput, { target: { value: "80" } });
    fireEvent.submit(form);
    expect(onRegisterMacro).not.toHaveBeenCalled();
    expect(screen.getByText("锁定窗口时长需在 0 至 72 小时之间")).toBeTruthy();

    fireEvent.change(beforeInput, { target: { value: "" } });
    fireEvent.change(afterInput, { target: { value: "" } });
    fireEvent.submit(form);
    expect(onRegisterMacro).toHaveBeenCalledWith(
      expect.objectContaining({
        event_name: "非农就业",
        lock_window_hours_before: 2,
        lock_window_hours_after: 2,
      })
    );
  });
});
